"""HomeLens: local router inventory, explicit metadata overrides, immutable samples."""

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import speedtest
from adapters import ADAPTERS, BRANDS
from adapters.zte import RouterAuthError, RouterBusy

ROOT = Path(__file__).resolve().parents[1]
# Local development only. Container deployments inject environment variables.
if (ROOT / ".env").is_file():
    for entry in (ROOT / ".env").read_text().splitlines():
        if entry.strip() and not entry.lstrip().startswith("#") and "=" in entry:
            key, value = entry.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
DATA = Path(os.environ.get("DATA_DIR", str(ROOT / ".local/data")))
WEB = Path(os.environ.get("WEB_DIR", str(Path(__file__).parent / "web"))).resolve()
USERNAME = os.environ.get("ADMIN_USERNAME", "")
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
LOCK = threading.RLock()
WAKE = threading.Event()
FAILURES = {}
logger = logging.getLogger("homelens")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()


@contextmanager
def connect():
    db = sqlite3.connect(DATA / "homelens.db", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    if not USERNAME or len(PASSWORD) < 8:
        raise ValueError("请设置 ADMIN_USERNAME 和至少 8 位的 ADMIN_PASSWORD")
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS routers(id TEXT PRIMARY KEY,name TEXT NOT NULL,config TEXT NOT NULL,snapshot TEXT NOT NULL DEFAULT '{}',checked REAL,generation INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,router_id TEXT NOT NULL REFERENCES routers(id) ON DELETE CASCADE,mac TEXT NOT NULL,data TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',group_name TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',visibility TEXT NOT NULL DEFAULT 'hide',notify_offline INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL,UNIQUE(router_id,mac));
        CREATE TABLE IF NOT EXISTS traffic(device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,at REAL NOT NULL,upload_bps REAL,download_bps REAL,state TEXT NOT NULL DEFAULT 'healthy',PRIMARY KEY(device_id,at));
        CREATE TABLE IF NOT EXISTS speedtests(day TEXT PRIMARY KEY,started REAL NOT NULL,finished REAL,state TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}');
        """)
        if "state" not in {r["name"] for r in db.execute("PRAGMA table_info(traffic)")}:
            db.execute(
                "ALTER TABLE traffic ADD COLUMN state TEXT NOT NULL DEFAULT 'healthy'"
            )
        # Preserve the devices visible under the old automatic rule when moving
        # to explicit switches. Previously hidden/offline devices stay hidden.
        for device in db.execute(
            "SELECT id,data,notify_offline FROM devices WHERE visibility='auto'"
        ).fetchall():
            was_visible = (
                json.loads(device["data"]).get("state") == "healthy"
                or bool(device["notify_offline"])
            )
            db.execute(
                "UPDATE devices SET visibility=? WHERE id=?",
                ("show" if was_visible else "hide", device["id"]),
            )
        # Keep legacy samples for rollback; the new chart reads traffic only.
        db.execute(
            "UPDATE speedtests SET state='error',finished=? WHERE state='running'",
            (time.time(),),
        )
        row = db.execute("SELECT value FROM settings WHERE key='account'").fetchone()
        old = json.loads(row["value"]) if row else {}
        if (
            old.get("username") != USERNAME
            or not old.get("salt")
            or not hmac.compare_digest(
                password_hash(PASSWORD, old["salt"]), old.get("hash", "")
            )
        ):
            salt = secrets.token_hex(16)
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('account',?)",
                (
                    json.dumps(
                        {
                            "username": USERNAME,
                            "salt": salt,
                            "hash": password_hash(PASSWORD, salt),
                        }
                    ),
                ),
            )
            db.execute("DELETE FROM sessions")
        if (
            os.environ.get("ROUTER_ADDRESS")
            and os.environ.get("ROUTER_PASSWORD")
            and not db.execute("SELECT 1 FROM routers LIMIT 1").fetchone()
        ):
            config = router_config(
                {
                    "address": os.environ["ROUTER_ADDRESS"],
                    "brand": os.environ.get("ROUTER_BRAND", "zte"),
                    "username": os.environ.get("ROUTER_USERNAME", "admin"),
                    "password": os.environ["ROUTER_PASSWORD"],
                }
            )
            db.execute(
                "INSERT INTO routers(id,name,config) VALUES(?,?,?)",
                (str(uuid.uuid4()), "", json.dumps(config)),
            )
    os.chmod(DATA / "homelens.db", 0o600)


def text(value, limit=200):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("文本格式或长度不正确")
    return value.strip()


def router_config(body, old=None):
    old = old or {}
    brand = body.get("brand", "zte")
    if brand not in ADAPTERS:
        raise ValueError("当前仅支持中兴路由器")
    address = text(body.get("address", "")).rstrip("/")
    u = urlsplit(address)
    try:
        port = u.port
    except ValueError:
        raise ValueError("管理地址端口无效") from None
    if (
        u.scheme not in ("http", "https")
        or not u.hostname
        or u.username
        or u.password
        or u.query
        or u.fragment
        or u.path not in ("", "/")
        or port == 0
    ):
        raise ValueError(
            "请填写完整的 HTTP/HTTPS 路由器管理地址，不包含账号、查询参数或页面路径"
        )
    password = body.get("password") or old.get("password", "")
    if not isinstance(password, str) or not password or len(password) > 256:
        raise ValueError("请填写路由器管理密码")
    return {
        "brand": brand,
        "address": address,
        "username": text(body.get("username", "admin")),
        "password": password,
    }


def sync(db, row, data, now):
    current = db.execute(
        "SELECT generation FROM routers WHERE id=?", (row["id"],)
    ).fetchone()
    if not current or current["generation"] != row["generation"]:
        return False
    existing = {
        r["mac"]: dict(r)
        for r in db.execute("SELECT * FROM devices WHERE router_id=?", (row["id"],))
    }
    incoming = {d["mac"]: d for d in data["devices"]}
    for address in set(existing) | set(incoming):
        old = existing.get(address)
        device = incoming.get(address) or {
            **json.loads(old["data"]),
            "state": "offline",
            "upload_bps": None,
            "download_bps": None,
        }
        did = (
            old["id"]
            if old
            else str(uuid.uuid5(uuid.NAMESPACE_URL, row["id"] + address))
        )
        db.execute(
            "INSERT INTO devices(id,router_id,mac,data,updated,visibility) VALUES(?,?,?,?,?,'hide') ON CONFLICT(router_id,mac) DO UPDATE SET data=excluded.data,updated=excluded.updated",
            (did, row["id"], address, json.dumps(device), now),
        )
        sample = db.execute(
            "SELECT at,upload_bps,download_bps FROM traffic WHERE device_id=? ORDER BY at DESC LIMIT 1",
            (did,),
        ).fetchone()
        online = device.get("state") == "healthy"
        if not sample or now - sample["at"] >= 60:
            db.execute(
                "INSERT OR IGNORE INTO traffic(device_id,at,upload_bps,download_bps,state) VALUES(?,?,?,?,?)",
                (
                    did,
                    now,
                    device.get("upload_bps") if online else None,
                    device.get("download_bps") if online else None,
                    device["state"],
                ),
            )
    data = {k: v for k, v in data.items() if k != "devices"}
    data.update(state="healthy", error=None)
    db.execute(
        "UPDATE routers SET snapshot=?,checked=? WHERE id=?",
        (json.dumps(data), now, row["id"]),
    )
    db.execute("DELETE FROM traffic WHERE at<?", (now - 7200,))
    return True


def status(db, private=False):
    now = time.time()
    routers = []
    for row in db.execute("SELECT * FROM routers ORDER BY rowid"):
        snapshot = json.loads(row["snapshot"])
        snapshot["state"] = (
            snapshot.get("state", "pending")
            if row["checked"] and now - row["checked"] < 180
            else "unknown"
        )
        result = {
            "id": row["id"],
            "name": row["name"] or snapshot.get("model") or "路由器",
            "checked_at": row["checked"],
            **snapshot,
        }
        if private:
            config = json.loads(row["config"])
            result["config"] = {k: v for k, v in config.items() if k != "password"}
            result["has_password"] = bool(config.get("password"))
            result["custom_name"] = row["name"]
        routers.append(result)
    index = {r["id"]: r for r in routers}
    devices = []
    for row in db.execute("SELECT * FROM devices ORDER BY rowid"):
        device = json.loads(row["data"])
        if index[row["router_id"]]["state"] != "healthy":
            device["state"] = "unknown"
        device.update(
            id=row["id"],
            router_id=row["router_id"],
            router_name=index[row["router_id"]]["name"],
            automatic_name=device.get("name", ""),
            name=row["name"] or device.get("name") or row["mac"],
            group=row["group_name"] or "家庭设备",
            visibility=row["visibility"],
            notify_offline=bool(row["notify_offline"]),
            updated_at=row["updated"],
        )
        if not private and row["visibility"] != "show":
            continue
        if private:
            device["custom_name"] = row["name"]
            device["custom_group"] = row["group_name"]
            device["notes"] = row["notes"]
        else:
            history = db.execute(
                "SELECT at,upload_bps,download_bps,state FROM traffic WHERE device_id=? AND at>=? ORDER BY at DESC LIMIT 120",
                (row["id"], now - 7200),
            ).fetchall()
            device["traffic"] = [dict(r) for r in reversed(history)]
        devices.append(device)
    counts = db.execute("SELECT data,router_id FROM devices").fetchall()
    for router in routers:
        router["online_count"] = (
            sum(
                r["router_id"] == router["id"]
                and json.loads(r["data"]).get("state") == "healthy"
                for r in counts
            )
            if router["state"] == "healthy"
            else None
        )
    online = sum(
        json.loads(r["data"]).get("state") == "healthy"
        and index[r["router_id"]]["state"] == "healthy"
        for r in counts
    )
    offline = sum(
        json.loads(r["data"]).get("state") == "offline"
        and index[r["router_id"]]["state"] == "healthy"
        for r in counts
    )
    return {
        "server_time": now,
        "routers": routers,
        "devices": devices,
        "totals": {
            "online": online,
            "known": len(counts),
            "offline": offline,
            "unknown": len(counts) - online - offline,
        },
        "brands": BRANDS,
        "speedtest": speedtest_status(db),
    }


def speedtest_status(db):
    enabled = os.environ.get("SPEEDTEST_ENABLED", "false").lower() == "true"
    latest = db.execute(
        "SELECT * FROM speedtests ORDER BY started DESC LIMIT 1"
    ).fetchone()
    good = db.execute(
        "SELECT * FROM speedtests WHERE state='healthy' ORDER BY started DESC LIMIT 1"
    ).fetchone()
    return {
        "enabled": enabled,
        "timezone": os.environ.get("SPEEDTEST_TIMEZONE", "Asia/Shanghai"),
        "state": latest["state"] if latest else "pending",
        "attempted_at": latest["started"] if latest else None,
        "checked_at": good["finished"] if good else None,
        **(json.loads(good["result"]) if good else {}),
    }


def speedtest_worker():
    while True:
        now = time.time()
        day = speedtest.due_day(
            now, os.environ.get("SPEEDTEST_TIMEZONE", "Asia/Shanghai")
        )
        claimed = False
        if day:
            with LOCK, connect() as db:
                claimed = (
                    db.execute(
                        "INSERT OR IGNORE INTO speedtests(day,started,state) VALUES(?,?,'running')",
                        (day, now),
                    ).rowcount
                    > 0
                )
        if claimed:
            state, result = "healthy", {}
            try:
                result = speedtest.run(DATA / "speedtest")
            except Exception:  # noqa: BLE001
                state = "error"
                logger.warning("Scheduled speed test failed")
            with LOCK, connect() as db:
                db.execute(
                    "UPDATE speedtests SET finished=?,state=?,result=? WHERE day=?",
                    (time.time(), state, json.dumps(result), day),
                )
        threading.Event().wait(15)


def worker():
    clients, retry, paused = {}, {}, set()
    while True:
        with LOCK, connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM routers")]
        for row in rows:
            key = (row["id"], row["generation"])
            if key in paused or time.time() < retry.get(key, 0):
                continue
            try:
                if key not in clients:
                    clients = {k: v for k, v in clients.items() if k[0] != row["id"]}
                    config = json.loads(row["config"])
                    clients[key] = ADAPTERS[config["brand"]](config)
                data = clients[key].poll()
                with LOCK, connect() as db:
                    sync(db, row, data, time.time())
                retry.pop(key, None)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, RouterAuthError):
                    paused.add(key)
                retry[key] = time.time() + (300 if isinstance(exc, RouterBusy) else 60)
                message = (
                    str(exc)
                    if isinstance(exc, (RouterBusy, RouterAuthError))
                    else "路由器数据暂时不可用，请检查管理地址、连接或会话"
                )
                with LOCK, connect() as db:
                    current = db.execute(
                        "SELECT generation,snapshot FROM routers WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                    if current and current["generation"] == row["generation"]:
                        old = json.loads(current["snapshot"])
                        db.execute(
                            "UPDATE routers SET snapshot=?,checked=? WHERE id=?",
                            (
                                json.dumps(
                                    {**old, "state": "unknown", "error": message}
                                ),
                                time.time(),
                                row["id"],
                            ),
                        )
                logger.warning(
                    "Router %s poll failed: %s", row["id"], type(exc).__name__
                )
        WAKE.wait(30)
        WAKE.clear()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def respond(self, code, data, cookie=None):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def session(self, db):
        token = next(
            (
                p.strip().split("=", 1)[1]
                for p in self.headers.get("Cookie", "").split(";")
                if p.strip().startswith("homelens_session=")
            ),
            "",
        )
        return (
            db.execute(
                "SELECT 1 FROM sessions WHERE hash=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
            is not None
        )

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 65536:
            raise ValueError("请求内容长度无效")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise TypeError("请求格式错误")
        return data

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def do_HEAD(self):
        self.handle_request()

    def handle_request(self):
        try:
            path = urlsplit(self.path).path
            method = self.command
            if (
                method not in ("GET", "HEAD")
                and self.headers.get("X-HomeLens-Request") != "1"
            ):
                return self.respond(403, {"error": "请求校验失败"})
            with LOCK, connect() as db:
                if path == "/healthz":
                    return self.respond(200, {"ok": True})
                if path == "/api/auth":
                    return self.respond(200, {"authenticated": self.session(db)})
                if path == "/api/status" and method == "GET":
                    return self.respond(200, status(db))
                if path == "/api/login" and method == "POST":
                    body = self.body()
                    now = time.time()
                    ip = self.client_address[0]
                    FAILURES[ip] = [t for t in FAILURES.get(ip, []) if t > now - 300]
                    if len(FAILURES[ip]) >= 10:
                        return self.respond(429, {"error": "尝试次数过多，请稍后再试"})
                    account = json.loads(
                        db.execute(
                            "SELECT value FROM settings WHERE key='account'"
                        ).fetchone()["value"]
                    )
                    supplied = body.get("password", "")
                    if (
                        not isinstance(supplied, str)
                        or len(supplied) > 1024
                        or body.get("username") != account["username"]
                        or not hmac.compare_digest(
                            password_hash(supplied, account["salt"]), account["hash"]
                        )
                    ):
                        FAILURES[ip].append(now)
                        return self.respond(401, {"error": "账号或密码不正确"})
                    FAILURES.pop(ip, None)
                    db.execute("DELETE FROM sessions WHERE expires<?", (now,))
                    token = secrets.token_urlsafe(32)
                    db.execute(
                        "INSERT INTO sessions VALUES(?,?)", (digest(token), now + 86400)
                    )
                    secure = "; Secure" if PUBLIC_URL.startswith("https://") else ""
                    return self.respond(
                        200,
                        {"ok": True},
                        f"homelens_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400{secure}",
                    )
                if path.startswith("/api/"):
                    if not self.session(db):
                        return self.respond(401, {"error": "请先登录"})
                    if path == "/api/logout" and method == "POST":
                        for token in self.headers.get("Cookie", "").split(";"):
                            if token.strip().startswith("homelens_session="):
                                db.execute(
                                    "DELETE FROM sessions WHERE hash=?",
                                    (digest(token.strip().split("=", 1)[1]),),
                                )
                        return self.respond(
                            200,
                            {"ok": True},
                            "homelens_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
                        )
                    if path == "/api/admin" and method == "GET":
                        return self.respond(200, status(db, True))
                    if path == "/api/routers" and method == "POST":
                        body = self.body()
                        config = router_config(body)
                        rid = str(uuid.uuid4())
                        db.execute(
                            "INSERT INTO routers(id,name,config) VALUES(?,?,?)",
                            (rid, text(body.get("name", "")), json.dumps(config)),
                        )
                        WAKE.set()
                        return self.respond(201, {"id": rid})
                    if path.startswith("/api/routers/"):
                        rid = path.rsplit("/", 1)[1]
                        old = db.execute(
                            "SELECT config FROM routers WHERE id=?", (rid,)
                        ).fetchone()
                        if not old:
                            return self.respond(404, {"error": "路由器不存在"})
                        if method == "PATCH":
                            body = self.body()
                            config = router_config(body, json.loads(old["config"]))
                            db.execute(
                                "UPDATE routers SET name=?,config=?,generation=generation+1 WHERE id=?",
                                (text(body.get("name", "")), json.dumps(config), rid),
                            )
                            WAKE.set()
                            return self.respond(200, {"ok": True})
                        if method == "DELETE":
                            db.execute("DELETE FROM routers WHERE id=?", (rid,))
                            return self.respond(200, {"ok": True})
                    if path.startswith("/api/devices/") and method == "PATCH":
                        did = path.rsplit("/", 1)[1]
                        body = self.body()
                        visibility = body.get("visibility", "hide")
                        if (
                            visibility not in ("show", "hide")
                            or type(body.get("notify_offline", False)) is not bool
                        ):
                            raise ValueError("设备显示设置无效")
                        cursor = db.execute(
                            "UPDATE devices SET name=?,group_name=?,notes=?,visibility=?,notify_offline=? WHERE id=?",
                            (
                                text(body.get("name", "")),
                                text(body.get("group", "")),
                                text(body.get("notes", ""), 1000),
                                visibility,
                                int(body.get("notify_offline", False)),
                                did,
                            ),
                        )
                        return self.respond(
                            200 if cursor.rowcount else 404,
                            {"ok": bool(cursor.rowcount)},
                        )
                    return self.respond(404, {"error": "接口不存在"})
            if method not in ("GET", "HEAD"):
                return self.respond(405, {"error": "不支持的请求"})
            relative = unquote(path).lstrip("/")
            if path in ("/", "/admin"):
                relative = "index.html"
            file = (WEB / relative).resolve()
            if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                return self.respond(404, {"error": "页面不存在"})
            raw = file.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(str(file))[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(raw)
        except (ValueError, TypeError, KeyError):
            self.respond(400, {"error": "请求参数无效"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("Request processing failed")
            self.respond(500, {"error": "服务暂时不可用"})

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.command, urlsplit(self.path).path)


if __name__ == "__main__":
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    if os.environ.get("SPEEDTEST_ENABLED", "false").lower() == "true":
        speedtest.due_day(
            time.time(), os.environ.get("SPEEDTEST_TIMEZONE", "Asia/Shanghai")
        )
    if os.environ.get("COLLECTOR_ENABLED", "true").lower() == "true":
        threading.Thread(target=worker, daemon=True).start()
    if os.environ.get("SPEEDTEST_ENABLED", "false").lower() == "true":
        threading.Thread(target=speedtest_worker, daemon=True).start()
    ThreadingHTTPServer(
        (
            os.environ.get("BIND_ADDRESS", "127.0.0.1"),
            int(os.environ.get("PORT", "18793")),
        ),
        Handler,
    ).serve_forever()
