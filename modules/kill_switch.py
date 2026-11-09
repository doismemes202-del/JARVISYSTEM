"""KILL SWITCH GLOBAL — tecla F1 (hook de teclado do Windows) ou comando remoto kill/abort.

Ao disparar:
  1. avisa os callbacks registrados (ex.: HUD web mostra 'SYSTEM HALTED');
  2. encerra processos registrados (Open Interpreter, etc.) e toda a árvore de filhos do Python;
  3. encerra instâncias do Open Interpreter, drivers de navegador automatizado (Selenium) e o Ollama;
  4. finaliza o processo Python com os._exit (mata todas as threads instantaneamente).

JARVIS_KILL_DRY_RUN=1 apenas registra o que seria encerrado (usado nos testes).
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Callable

import psutil

from . import config

log = logging.getLogger("jarvis.kill")

_lock = threading.Lock()
_triggered = threading.Event()
_registered: list[subprocess.Popen] = []
_callbacks: list[Callable[[str], None]] = []
_hotkey_handle = None
last_report: dict = {}

OLLAMA_IMAGES = ["ollama app.exe", "ollama.exe", "ollama_llama_server.exe"]
DRIVER_IMAGES = ["chromedriver.exe", "msedgedriver.exe", "geckodriver.exe"]
INTERPRETER_IMAGES = {"interpreter.exe"}


def register_process(proc: subprocess.Popen) -> None:
    """Registra um subprocesso para ser encerrado pelo kill switch."""
    with _lock:
        _registered.append(proc)


def on_kill(callback: Callable[[str], None]) -> None:
    _callbacks.append(callback)


def is_triggered() -> bool:
    return _triggered.is_set()


def reset() -> None:
    """Somente para testes (dry-run)."""
    _triggered.clear()


def _collect_targets(include_ollama: bool | None = None) -> dict[int, str]:
    me = os.getpid()
    targets: dict[int, str] = {}

    with _lock:
        for p in _registered:
            if p.poll() is None:
                targets[p.pid] = "registrado"
                try:
                    for c in psutil.Process(p.pid).children(recursive=True):
                        targets[c.pid] = f"filho de {p.pid}"
                except psutil.Error:
                    pass

    try:
        for c in psutil.Process(me).children(recursive=True):
            targets.setdefault(c.pid, "filho do JARVIS")
    except psutil.Error:
        pass

    wanted = {n.lower() for n in DRIVER_IMAGES} | INTERPRETER_IMAGES
    if config.KILL_OLLAMA if include_ollama is None else include_ollama:
        wanted |= {n.lower() for n in OLLAMA_IMAGES}

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid == me:
                continue
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info["cmdline"] or []).lower()
            if name in wanted:
                targets.setdefault(proc.pid, name)
            elif "python" in name and ("-m interpreter" in cmd or "tool_fusion --oi" in cmd
                                        or "modules.tool_fusion" in cmd):
                targets.setdefault(proc.pid, "open-interpreter")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return targets


def _kill(targets: dict[int, str]) -> None:
    procs = []
    for pid in targets:
        try:
            p = psutil.Process(pid)
            p.kill()
            procs.append(p)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            # fallback: taskkill força a árvore
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    psutil.wait_procs(procs, timeout=3)


def trigger(reason: str = "F1", dry_run: bool | None = None) -> dict:
    """Dispara o encerramento de emergência. Idempotente."""
    global last_report
    dry = config.KILL_DRY_RUN if dry_run is None else dry_run
    if _triggered.is_set():
        return last_report
    _triggered.set()

    log.critical("☠ KILL SWITCH ACIONADO (%s) — encerrando todos os subsistemas%s",
                 reason, " [DRY-RUN]" if dry else "")
    for cb in list(_callbacks):
        try:
            cb(reason)
        except Exception:
            pass

    targets = _collect_targets()
    last_report = {"reason": reason, "dry_run": dry, "targets": targets, "time": time.time()}
    for pid, why in targets.items():
        log.warning("  → %s PID %s (%s)", "seria encerrado" if dry else "encerrando", pid, why)

    if dry:
        return last_report

    time.sleep(0.4)  # dá tempo do aviso chegar ao celular
    _kill(targets)
    logging.shutdown()
    os._exit(137)


_abort_callbacks: list[Callable[[str], None]] = []


def on_abort(callback: Callable[[str], None]) -> None:
    _abort_callbacks.append(callback)


def abort(reason: str = "F1", dry_run: bool | None = None) -> dict:
    """PARADA DE EMERGÊNCIA SUAVE (modo CLI): cancela gerações, encerra Open Interpreter,
    drivers de navegador e subprocessos — mas mantém o JARVIS e o Ollama vivos (estado IDLE)."""
    dry = config.KILL_DRY_RUN if dry_run is None else dry_run
    for cb in list(_abort_callbacks):           # primeiro: cancela a geração em andamento
        try:
            cb(reason)
        except Exception:
            pass
    targets = _collect_targets(include_ollama=False)
    if targets:
        log.warning("⛔ ABORT (%s): %s %d processo(s)%s", reason, "encerraria" if dry else "encerrando",
                    len(targets), " [DRY-RUN]" if dry else "")
        if not dry:
            _kill(targets)
    with _lock:
        _registered[:] = [p for p in _registered if p.poll() is None]
    return {"reason": reason, "dry_run": dry, "targets": targets}


def start_listener(hotkey: str | None = None, action: Callable[[str], object] | None = None) -> None:
    """Registra o hook global da tecla (padrão F1). Não bloqueia.
    action: função chamada com o motivo (padrão: trigger = encerramento total)."""
    global _hotkey_handle
    import keyboard  # import tardio: só exige a lib se o hotkey estiver ativo

    key = hotkey or config.KILL_HOTKEY
    target = action or trigger

    def _fire():
        threading.Thread(target=target, args=(key.upper(),), daemon=True, name="kill-switch").start()

    _hotkey_handle = keyboard.add_hotkey(key, _fire, suppress=False, trigger_on_release=False)
    log.info("Kill switch armado na tecla %s (global).", key.upper())


def stop_listener() -> None:
    global _hotkey_handle
    if _hotkey_handle is not None:
        import keyboard
        try:
            keyboard.remove_hotkey(_hotkey_handle)
        except (KeyError, ValueError):
            pass
        _hotkey_handle = None
