import copy
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import app
from adapters.zte import ZTE, RouterBusy, bps, normalize, records


class AdapterTests(unittest.TestCase):
    def test_xml_pairs_and_units(self):
        raw = "<root><OBJ><Instance><ParaName>A</ParaName><ParaValue>1</ParaValue><ParaName>B</ParaName><ParaValue/></Instance></OBJ></root>"
        self.assertEqual(records(raw, "OBJ"), [{"A": "1", "B": ""}])
        self.assertEqual(bps("12.5Kbps"), 12500)
        self.assertIsNone(bps("unknown"))

    def test_mesh_wireless_overrides_lan_classification(self):
        d = normalize(
            {
                "MACAddress": "AA-BB-CC-DD-EE-FF",
                "InterfaceType": "1",
                "UploadSpeed": "5.2",
                "Active": "1",
            },
            {"AccessType": "2", "Rssi": "-61"},
        )
        self.assertEqual(d["connection"], "5GHz")
        self.assertEqual(d["signal"], -61)
        self.assertEqual(d["upload_bps"], 5200)
        self.assertEqual(d["mac"], "aa:bb:cc:dd:ee:ff")

    def test_login_does_not_preempt_another_admin(self):
        client = ZTE({"address": "http://192.168.5.1", "password": "test"})
        replies = [
            "",
            json.dumps({"data": {}}),
            json.dumps({"logintoken": "abc", "_sessionToken": "x"}),
            json.dumps({"login_need_refresh": True}),
            "<LOGIN_PREEMPT_ID/>",
        ]
        with patch.object(client, "request", side_effect=replies) as request:
            with self.assertRaises(RouterBusy):
                client.login()
            self.assertFalse(
                any("login_preempt" in c.args[0] for c in request.call_args_list)
            )
        self.assertFalse(client.authenticated)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(app, "DATA", Path(self.tmp.name)),
            patch.object(app, "USERNAME", "test"),
            patch.object(app, "PASSWORD", "test-password"),
            patch.dict(os.environ, {"ROUTER_ADDRESS": "", "ROUTER_PASSWORD": ""}),
        ]
        for p in self.patches:
            p.start()
        app.init_db()
        self.now = time.time()
        with app.connect() as db:
            db.execute(
                "INSERT INTO routers(id,name,config) VALUES(?,?,?)",
                (
                    "r",
                    "",
                    json.dumps(
                        {
                            "password": "secret",
                            "address": "http://router",
                            "brand": "zte",
                        }
                    ),
                ),
            )
        self.sample = {
            "devices": [
                {
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "name": "automatic",
                    "state": "healthy",
                    "upload_bps": 10,
                }
            ],
            "model": "ZTE",
            "wan_state": "healthy",
            "nodes": [],
        }

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def sync(self, db, at, data=None):
        row = db.execute("SELECT * FROM routers").fetchone()
        return app.sync(
            db, row, copy.deepcopy(self.sample if data is None else data), at
        )

    def test_metadata_survives_poll_and_public_hides_secrets(self):
        with app.connect() as db:
            self.sync(db, self.now)
            db.execute(
                "UPDATE devices SET name='custom',group_name='room',notes='private',visibility='show'"
            )
            self.sync(db, self.now + 30)
            public = app.status(db)
            self.assertEqual(public["devices"][0]["name"], "custom")
            self.assertEqual(public["routers"][0]["online_count"], 1)
            self.assertNotIn("secret", json.dumps(public))
            self.assertNotIn("private", json.dumps(public))
            self.assertEqual(len(public["devices"][0]["traffic"]), 1)

    def test_traffic_is_not_rewritten_and_missing_device_is_offline(self):
        with app.connect() as db:
            self.sync(db, self.now)
            self.sync(db, self.now + 60, {"devices": [], "model": "ZTE"})
            points = db.execute(
                "SELECT at,upload_bps FROM traffic ORDER BY at"
            ).fetchall()
            self.assertEqual(
                [(r["at"], r["upload_bps"]) for r in points],
                [(self.now, 10), (self.now + 60, None)],
            )
            self.assertEqual(app.status(db)["totals"]["offline"], 1)

    def test_new_devices_require_explicit_visibility(self):
        with app.connect() as db:
            self.sync(db, self.now)
            self.assertEqual(app.status(db)["devices"], [])
            self.assertEqual(len(app.status(db, True)["devices"]), 1)
            self.assertFalse(app.status(db, True)["devices"][0]["allow_offline"])
            db.execute("UPDATE devices SET visibility='show'")
            self.assertEqual(len(app.status(db)["devices"]), 1)
            self.sync(db, self.now + 70)
            self.assertEqual(len(app.status(db)["devices"]), 1)

    def test_allow_offline_survives_poll(self):
        with app.connect() as db:
            self.sync(db, self.now)
            db.execute("UPDATE devices SET visibility='show',allow_offline=1")
            self.sync(db, self.now + 30)
            public = app.status(db)
            self.assertTrue(public["devices"][0]["allow_offline"])

    def test_upgrade_keeps_previously_visible_devices_and_legacy_samples(self):
        with app.connect() as db:
            self.sync(db, self.now)
            db.execute("UPDATE devices SET visibility='auto'")
            db.execute(
                "CREATE TABLE samples(device_id TEXT,at REAL,state TEXT)"
            )
            db.execute(
                "INSERT INTO samples SELECT id,?, 'healthy' FROM devices",
                (self.now,),
            )
        app.init_db()
        with app.connect() as db:
            self.assertEqual(
                db.execute("SELECT visibility FROM devices").fetchone()[0], "show"
            )
            self.assertEqual(len(app.status(db)["devices"]), 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
            db.execute(
                "UPDATE devices SET visibility='auto',data=?",
                (json.dumps({"state": "offline"}),),
            )
        app.init_db()
        with app.connect() as db:
            self.assertEqual(
                db.execute("SELECT visibility FROM devices").fetchone()[0], "hide"
            )

    def test_router_failure_means_unknown_not_offline(self):
        with app.connect() as db:
            self.sync(db, self.now - 200)
            public = app.status(db, True)
            self.assertEqual(public["devices"][0]["state"], "unknown")
            self.assertEqual(public["totals"]["offline"], 0)
            self.assertEqual(public["totals"]["unknown"], 1)

    def test_stale_configuration_result_cannot_overwrite(self):
        with app.connect() as db:
            row = db.execute("SELECT * FROM routers").fetchone()
            db.execute("UPDATE routers SET generation=2")
            self.assertFalse(app.sync(db, row, copy.deepcopy(self.sample), self.now))
            self.assertEqual(
                db.execute("SELECT count(*) FROM devices").fetchone()[0], 0
            )

    def test_http_authentication_and_masked_configuration(self):
        import http.cookiejar
        import threading
        import urllib.error
        import urllib.request

        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        client = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                client.open(base + "/api/admin")
            self.assertEqual(denied.exception.code, 401)
            body = json.dumps(
                {"username": "test", "password": "test-password"}
            ).encode()
            with self.assertRaises(urllib.error.HTTPError) as csrf:
                client.open(urllib.request.Request(base + "/api/login", data=body))
            self.assertEqual(csrf.exception.code, 403)
            request = urllib.request.Request(
                base + "/api/login", data=body, headers={"X-HomeLens-Request": "1"}
            )
            with patch.object(app, "PUBLIC_URL", "http://localhost"), client.open(
                request
            ) as response:
                self.assertTrue(json.load(response)["ok"])
            with client.open(base + "/api/admin") as response:
                payload = json.load(response)
            self.assertTrue(payload["routers"][0]["has_password"])
            self.assertNotIn("secret", json.dumps(payload))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_router_url_validation_and_password_preservation(self):
        for address in [
            "ftp://router",
            "http://user:pass@router",
            "http://router/login",
            "http://router?x=1",
        ]:
            with self.assertRaises(ValueError):
                app.router_config({"address": address, "password": "x"})
        self.assertEqual(
            app.router_config(
                {"address": "http://router", "password": ""}, {"password": "original"}
            )["password"],
            "original",
        )


if __name__ == "__main__":
    unittest.main()
