"""
████  THE JARVIS SYSTEM — backend core / servidor web  ████

FastAPI + WebSockets (porta 8000) · telemetria CPU/RAM/GPU/VRAM · kill switch.
A orquestração (roteamento, agentes, FRIDAY QA) fica em modules/core.py e é compartilhada com a CLI.

    python jarvis_master_system.py     → servidor web standalone (F1 = encerramento total)
    python jarvis_cli.py --web         → CLI + este servidor em background (F1 = parada → IDLE)
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from modules import config, core, kill_switch, llm, telemetry

log = logging.getLogger("jarvis")

CLI_MODE = False             # True quando hospedado pela jarvis_cli.py
engine = core.Engine()       # a CLI substitui pelo seu próprio Engine (memória compartilhada)
voice = None


# =============================================================================== HUB (WebSockets)
class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.history: deque[dict] = deque(maxlen=300)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        for msg in list(self.history):
            await ws.send_text(json.dumps(msg, ensure_ascii=False))

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, msg: dict, keep: bool = True) -> None:
        msg.setdefault("ts", datetime.now().strftime("%H:%M:%S"))
        if keep:
            self.history.append(msg)
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                self.clients.discard(ws)

    def broadcast_threadsafe(self, msg: dict, keep: bool = True) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg, keep), self.loop)


hub = Hub()


class HubLogHandler(logging.Handler):
    """Espelha os logs no terminal web em tempo real."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            hub.broadcast_threadsafe({"type": "log", "level": record.levelname,
                                      "name": record.name.replace("jarvis.", "").upper(),
                                      "text": record.getMessage()})
        except Exception:
            pass


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s │ %(levelname)-7s │ %(name)-14s │ %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root = logging.getLogger("jarvis")
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.propagate = False


# =============================================================================== TELEMETRIA
_ollama_state = {"online": False, "loaded": []}


def snapshot() -> dict:
    data = {"type": "telemetry", **telemetry.snapshot(),
            "ollama": _ollama_state["online"], "models": _ollama_state["loaded"],
            "voice": bool(voice and voice.ready.is_set()),
            "hotkey": config.KILL_HOTKEY.upper() if config.KILL_HOTKEY_ENABLED else None,
            "clients": len(hub.clients)}
    return data


async def telemetry_loop() -> None:
    tick = 0
    while True:
        if tick % 5 == 0:
            _ollama_state["online"] = await asyncio.to_thread(llm.is_online, 1.5)
            _ollama_state["loaded"] = await asyncio.to_thread(llm.loaded_models) if _ollama_state["online"] else []
        if hub.clients:
            await hub.broadcast(snapshot(), keep=False)
        tick += 1
        await asyncio.sleep(1.0)


# =============================================================================== COMANDOS
class AgentTracker:
    """Converte progress(agente, ação) do Engine em eventos busy/idle para o HUD."""

    def __init__(self) -> None:
        self.current: str | None = None

    def progress(self, agent: str, action: str) -> None:
        if agent != self.current:
            if self.current:
                hub.broadcast_threadsafe({"type": "agent", "agent": self.current, "state": "idle"}, keep=False)
            hub.broadcast_threadsafe({"type": "agent", "agent": agent, "state": "busy"}, keep=False)
            self.current = agent
            log.info("%s…", action)

    def done(self) -> None:
        if self.current:
            hub.broadcast_threadsafe({"type": "agent", "agent": self.current, "state": "idle"}, keep=False)


async def respond(agent: str, text: str, source: str, meta: str = "") -> None:
    await hub.broadcast({"type": "response", "agent": agent, "text": text, "meta": meta})
    if source == "voice" and voice:
        threading.Thread(target=voice.say, args=(text,), daemon=True).start()


async def handle_command(text: str, source: str = "web") -> None:
    text = (text or "").strip()
    if not text:
        return
    low = text.lower()
    if core.is_kill(text):                                # nunca espera a GPU
        action = kill_switch.abort if CLI_MODE else kill_switch.trigger
        threading.Thread(target=action, args=(f"{source.upper()}:{text}",), daemon=True).start()
        if CLI_MODE:
            await hub.broadcast({"type": "kill", "reason": f"{source.upper()}:{text} (parada → IDLE)"})
        return
    if low == "/shutdown":
        threading.Thread(target=kill_switch.trigger, args=(f"{source.upper()}:/shutdown",), daemon=True).start()
        return

    await hub.broadcast({"type": "user", "source": source, "text": text})
    try:
        if low == "/status":
            await respond("jarvis", await status_report(), source)
        elif low == "/agents":
            await respond("jarvis", "\n".join(f"{a['icon']} [{a['label']}] — {a['role']}"
                                              for a in core.AGENTS.values()), source)
        elif low.startswith("/voice"):
            await toggle_voice(not low.endswith("off"))
            await respond("jarvis", f"Voz {'ativada' if not low.endswith('off') else 'desativada'}.", source)
        else:
            tracker = AgentTracker()
            try:
                reply = await asyncio.to_thread(engine.handle, text, tracker.progress)
            finally:
                tracker.done()
            await respond(reply.agent, reply.as_text(), source, reply.meta)
    except llm.Cancelled:
        await respond("jarvis", "⛔ Geração cancelada (parada de emergência).", source)
    except (ValueError, llm.LLMError) as e:
        log.warning("%s", e)
        await respond("jarvis", f"⚠️ {e}", source)
    except Exception as e:
        log.exception("Falha ao processar comando")
        await respond("jarvis", f"⚠️ Falha interna: {e}", source)


async def status_report() -> str:
    online = await asyncio.to_thread(llm.is_online)
    installed = await asyncio.to_thread(llm.installed_models) if online else []
    loaded = await asyncio.to_thread(llm.loaded_models) if online else []
    ok = lambda b: "ONLINE " if b else "OFFLINE"
    in_vram = ", ".join(f"{m['name']} {m['vram_gb']}GB" for m in loaded) or "—"
    g = telemetry.gpu()
    lines = [
        "DIAGNÓSTICO DOS SISTEMAS",
        f"  Ollama ........ {ok(online)}  {config.OLLAMA_URL}",
        f"  jarvis ........ {ok(llm.has_model(config.JARVIS_MODEL, installed))}  ({config.JARVIS_MODEL})",
        f"  friday ........ {ok(llm.has_model(config.FRIDAY_MODEL, installed))}  ({config.FRIDAY_MODEL})",
        f"  visão ......... {ok(llm.has_model(config.VISION_MODEL, installed))}  ({config.VISION_MODEL})",
        f"  Na VRAM ....... {in_vram}",
        f"  GPU ........... {g.name if g.ok else 'N/A'}",
        f"  Voz ........... {ok(bool(voice and voice.ready.is_set()))}",
        f"  Kill switch ... {config.KILL_HOTKEY.upper() if config.KILL_HOTKEY_ENABLED else 'só remoto'}"
        f"{' (parada → IDLE, CLI)' if CLI_MODE else ''}{' [DRY-RUN]' if config.KILL_DRY_RUN else ''}",
        f"  Sheets ........ {'credencial OK' if config.GOOGLE_CREDENTIALS.exists() else 'sem credencial'}",
        f"  Fusion Scripts  {config.FUSION_SCRIPTS_DIR}",
        f"  Clientes HUD .. {len(hub.clients)}",
    ]
    return "\n".join(lines)


async def toggle_voice(on: bool) -> None:
    global voice
    if on and not voice:
        from modules.voice_engine import VoiceEngine
        loop = asyncio.get_running_loop()
        voice = VoiceEngine(lambda t: asyncio.run_coroutine_threadsafe(handle_command(t, "voice"), loop))
        voice.start()
    elif not on and voice:
        voice.stop()
        voice = None


# =============================================================================== APP
@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    root = logging.getLogger("jarvis")
    hub_handler = HubLogHandler()
    root.addHandler(hub_handler)
    kill_switch.on_kill(lambda reason: hub.broadcast_threadsafe({"type": "kill", "reason": reason}))
    if not CLI_MODE:                     # na CLI quem arma o F1 (modo parada → IDLE) é a própria CLI
        kill_switch.on_abort(lambda reason: engine.abort())
        if config.KILL_HOTKEY_ENABLED:
            try:
                kill_switch.start_listener(config.KILL_HOTKEY)
            except Exception as e:
                log.error("Não foi possível armar o hotkey %s: %s", config.KILL_HOTKEY, e)
        if config.VOICE_ENABLED:
            await toggle_voice(True)
    task = asyncio.create_task(telemetry_loop())
    g = telemetry.gpu()
    log.info("J.A.R.V.I.S ONLINE — %s · Friday %s", g.name if g.ok else "CPU",
             "ATIVA" if config.FRIDAY_ENABLED else "DESATIVADA")
    yield
    task.cancel()
    root.removeHandler(hub_handler)
    if not CLI_MODE:
        kill_switch.stop_listener()
        if voice:
            voice.stop()


app = FastAPI(title="THE JARVIS SYSTEM", lifespan=lifespan, docs_url=None, redoc_url=None)


def check_token(token: str) -> None:
    if not secrets.compare_digest(token or "", config.TOKEN):
        raise HTTPException(status_code=401, detail="token inválido")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((config.TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict:
    return {"status": "online", "time": time.time(), "kill_armed": config.KILL_HOTKEY_ENABLED,
            "mode": "cli" if CLI_MODE else "server"}


@app.get("/api/telemetry")
async def api_telemetry(token: str = "") -> dict:
    check_token(token)
    return snapshot()


@app.post("/api/kill")
async def api_kill(token: str = "") -> dict:
    check_token(token)
    action = kill_switch.abort if CLI_MODE else kill_switch.trigger
    threading.Thread(target=action, args=("HTTP:/api/kill",), daemon=True).start()
    return {"status": "aborting" if CLI_MODE else "killing"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = "") -> None:
    if not secrets.compare_digest(token or "", config.TOKEN):
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    log.info("Terminal web conectado (%s).", ws.client.host if ws.client else "?")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "cmd", "text": raw}
            if msg.get("type") == "cmd":
                asyncio.create_task(handle_command(str(msg.get("text", "")), "web"))
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()
    ip = lan_ip()
    print("\n" + "═" * 64)
    print("  THE JARVIS SYSTEM  //  JARVIS · MONDAY · ROMEU · PAPA · PLATO · FRIDAY")
    print("═" * 64)
    print(f"  PC:       http://127.0.0.1:{config.PORT}/?token={config.TOKEN}")
    print(f"  Celular:  http://{ip}:{config.PORT}/?token={config.TOKEN}")
    print(f"  Kill:     tecla {config.KILL_HOTKEY.upper()}  ou  'kill' no terminal web")
    print("  Dica:     a interface principal agora é a CLI → python jarvis_cli.py --web")
    print("═" * 64 + "\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning", ws_ping_interval=20)


if __name__ == "__main__":
    main()
