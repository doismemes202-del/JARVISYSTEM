"""Smoke test do servidor já em execução na porta 8000.

    python tests/smoke_server.py
Valida: /api/health, rejeição de token inválido, WebSocket (/status + telemetria) e o HUD.
"""
import asyncio
import json
import sys
from pathlib import Path

import requests
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules import config  # noqa: E402

BASE = f"http://127.0.0.1:{config.PORT}"


async def ws_check() -> dict:
    got = {"telemetry": None, "status": None}
    async with websockets.connect(f"ws://127.0.0.1:{config.PORT}/ws?token={config.TOKEN}") as ws:
        await ws.send(json.dumps({"type": "cmd", "text": "/status"}))
        for _ in range(60):
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if msg["type"] == "telemetry":
                got["telemetry"] = msg
            if msg["type"] == "response" and "DIAGNÓSTICO" in msg["text"]:
                got["status"] = msg["text"]
            if all(got.values()):
                break
    return got


def main() -> int:
    results = []
    h = requests.get(f"{BASE}/api/health", timeout=5).json()
    results.append(("health", h.get("status") == "online"))
    results.append(("token inválido → 401", requests.get(f"{BASE}/api/telemetry?token=x", timeout=5).status_code == 401))
    html = requests.get(BASE, timeout=5).text
    results.append(("HUD index.html", "xterm" in html and "KILL (F1)" in html))
    got = asyncio.run(ws_check())
    results.append(("WebSocket telemetria", bool(got["telemetry"])))
    results.append(("WebSocket /status", bool(got["status"])))
    for name, ok in results:
        print(f"  {'✔' if ok else '✘'} {name}")
    if got["telemetry"]:
        t = got["telemetry"]
        print(f"\n  CPU {t['cpu']}% · RAM {t['ram_pct']}% · GPU {t.get('gpu_util')}% · "
              f"VRAM {t.get('vram_used_gb')}/{t.get('vram_total_gb')} GB · {t.get('gpu_temp')}°C")
    if got["status"]:
        print("\n" + got["status"])
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
