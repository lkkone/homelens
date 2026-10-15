"""Read-only adapter for the ZTE SR1001 / vue_topo firmware family."""

import hashlib
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class RouterError(Exception):
    pass


class RouterBusy(RouterError):
    pass


class RouterAuthError(RouterError):
    pass


def records(raw, name):
    root = ET.fromstring(raw)
    return [
        dict(
            zip(
                (p.text for p in node.findall("ParaName")),
                (p.text or "" for p in node.findall("ParaValue")),
            )
        )
        for node in root.findall(f".//{name}/Instance")
    ]


def number(value, default=None):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def mac(value):
    value = str(value or "").replace("-", ":").lower()
    return value if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value) else ""


def bps(value, unit=1):
    if value is None or value == "":
        return None
    if number(value) is not None:
        return max(0, number(value) * unit)
    found = re.fullmatch(r"\s*([\d.]+)\s*([KMG]?)bps\s*", str(value), re.IGNORECASE)
    return (
        float(found[1]) * {"": 1, "K": 1000, "M": 1e6, "G": 1e9}[found[2].upper()]
        if found
        else None
    )


def normalize(lan, topo):
    address = mac(lan.get("MACAddress") or topo.get("MacAddr"))
    access = str(topo.get("AccessType", ""))
    wired = access == "0" if access else str(lan.get("InterfaceType")) == "1"
    signal = number(topo.get("Rssi", lan.get("DirectRssi") or lan.get("Rssi")))
    signal = signal if not wired and signal is not None and -127 <= signal < 0 else None
    # ZTE AliasName is the interface label (LAN3 / SSID1), not the device alias.
    name = lan.get("DevName") or topo.get("HostName") or lan.get("HostName") or address
    return {
        "mac": address,
        "name": name,
        "ip": topo.get("IpAddr") or lan.get("IPAddress") or "",
        "state": "healthy" if topo or str(lan.get("Active")) == "1" else "offline",
        "connection": (
            "有线"
            if wired
            else {"1": "2.4GHz", "2": "5GHz", "3": "6GHz"}.get(
                access, lan.get("Band") or "Wi-Fi"
            )
        ),
        "signal": signal,
        "ap": topo.get("parentDevName")
        or topo.get("ParentDeviceName")
        or lan.get("ParentDeviceName")
        or "",
        "brand": lan.get("Brand") or lan.get("DisplayedVendor") or "",
        "model": lan.get("Model") or "",
        "upload_bps": bps(lan.get("UploadSpeed"), 1000),
        "download_bps": bps(lan.get("DownloadSpeed"), 1000),
        "sent_bytes": number(lan.get("BytesSend")),
        "received_bytes": number(lan.get("BytesReceived")),
        "online_seconds": number(lan.get("OnlineTimes")),
        "last_online": lan.get("ActiveTime") or "",
        "last_offline": lan.get("InactiveTime") or "",
    }


class ZTE:
    def __init__(self, config):
        self.config = config
        self.base = config["address"].rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            NoRedirect(),
        )
        self.authenticated = False
        self.model = "中兴路由器"
        self.version = ""

    def request(self, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            data=urllib.parse.urlencode(body).encode() if body is not None else None,
            headers={
                "Referer": self.base + "/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise RouterError("路由器响应过大")
                return raw.decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RouterError(f"路由器返回 HTTP {exc.code}") from None

    def login(self):
        self.request("/")
        info = json.loads(self.request("/?_type=hiddenScene&_tag=initial_info_json"))[
            "data"
        ]
        env = info.get("OBJ_GLOBAL_ENV", {})
        self.model = env.get("WEBTitle") or "中兴路由器"
        self.version = env.get("SoftwareVer") or ""
        token = json.loads(self.request("/?_type=loginsceneData&_tag=login_token_json"))
        body = {
            "Username": self.config.get("username") or info.get("user") or "admin",
            "Password": hashlib.sha256(
                (self.config["password"] + token["logintoken"]).encode()
            ).hexdigest(),
            "action": "login",
            "Frm_Logintoken": "",
            "captchaCode": "",
            "_sessionTOKEN": token["_sessionToken"],
        }
        result = json.loads(self.request("/?_type=loginData&_tag=login_entry", body))
        if not result.get("login_need_refresh"):
            raise RouterAuthError("路由器登录失败，请检查账号密码或管理锁定状态")
        interface = self.request("/?_type=hiddenData&_tag=vue_userif_data")
        if "LOGIN_PREEMPT_ID" in interface:
            raise RouterBusy("其他管理页面正在登录，等待会话释放后自动重试")
        if "LOGIN_CHGPWD_ID" in interface:
            raise RouterAuthError("请先在路由器管理页完成首次密码设置")
        self.authenticated = True

    def xml(self, tag, name):
        raw = self.request("/?_type=vueData&_tag=" + tag)
        if "SessionTimeout" in raw or raw.lstrip().lower().startswith("<!doctype"):
            self.authenticated = False
            raise RouterError("管理会话已过期，将重新登录")
        return records(raw, name)

    def poll(self):
        if not self.authenticated:
            self.login()
        raw = self.xml("localnet_lan_info_lua", "OBJ_LAN_INFO_ID")
        clients = json.loads(
            self.request("/?_type=vueData&_tag=vue_topo_data&Action=GetALLClients")
        ).get("clientsData", {})
        aps = json.loads(
            self.request("/?_type=vueData&_tag=vue_topo_data&Action=GetALLAP")
        ).get("apData", {})
        topo = {
            mac(v.get("MacAddr")): v
            for v in clients.values()
            if isinstance(v, dict) and mac(v.get("MacAddr"))
        }
        nodes = [v for v in aps.values() if isinstance(v, dict)]
        node_macs = {mac(v.get("MacAddr")) for v in nodes}
        devices = {}
        for item in raw:
            address = mac(item.get("MACAddress"))
            if not address or address in node_macs:
                continue
            device = normalize(item, topo.get(address, {}))
            if address not in devices or device["state"] == "healthy":
                devices[address] = device
        for address, item in topo.items():
            if address not in devices and address not in node_macs:
                devices[address] = normalize({}, item)
        # A parent AP name may be supplied as its MAC; resolve it from the topology.
        names = {
            mac(v.get("MacAddr")): v.get("DeviceName") or v.get("ModelName")
            for v in nodes
        }
        for address, device in devices.items():
            parent = (
                topo.get(address, {}).get("parent")
                or topo.get(address, {}).get("parentMac")
                or topo.get(address, {}).get("ParentMac")
            )
            if not device["ap"] and mac(parent) in names:
                device["ap"] = names[mac(parent)]
        summary = self.xml(
            "vue_home_device_data_no_update_sess&IF_OP=refresh", "OBJ_HOME_BASICINFO_ID"
        )
        summary = summary[0] if summary else {}
        return {
            "model": self.model,
            "version": self.version,
            "wan_state": (
                "healthy" if summary.get("WANStatus") == "Connected" else "offline"
            ),
            "upload_bps": bps(summary.get("WANUpRate")),
            "download_bps": bps(summary.get("WANDownRate")),
            "devices": list(devices.values()),
            "nodes": [
                {
                    "name": v.get("DeviceName") or v.get("ModelName"),
                    "model": v.get("ModelName"),
                    "ip": v.get("IpAddr"),
                    "mac": mac(v.get("MacAddr")),
                    "role": v.get("role"),
                    "version": v.get("SoftwareVer"),
                    "link_speed": v.get("LinkSpeed"),
                }
                for v in nodes
            ],
        }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RouterError("管理地址发生重定向，请填写最终的路由器地址")
