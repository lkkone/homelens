"""One durable speed test per local midnight; no shell commands or daytime catch-up."""

import json
import math
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo


def due_day(now, timezone="Asia/Shanghai"):
    local = datetime.fromtimestamp(now, ZoneInfo(timezone))
    return local.date().isoformat() if local.hour == 0 and local.minute < 5 else None


def measurement(raw):
    body = json.loads(raw)
    values = {
        "download_bps": body["download"]["bandwidth"] * 8,
        "upload_bps": body["upload"]["bandwidth"] * 8,
        "latency_ms": body["ping"]["latency"],
    }
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or v < 0
        for v in values.values()
    ):
        raise ValueError("Invalid speed measurement")
    values["server_name"] = str(body.get("server", {}).get("name", ""))[:100]
    return values


def run(directory):
    env = {
        k: v
        for k, v in os.environ.items()
        if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")
    }
    directory.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(directory)
    result = subprocess.run(
        [
            os.environ.get("SPEEDTEST_BIN", "speedtest"),
            "--accept-license",
            "--accept-gdpr",
            "--format=json",
            "--progress=no",
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
        env=env,
    )
    return measurement(result.stdout)
