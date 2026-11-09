"""Cliente da API do Ollama (síncrono, com streaming interno para permitir cancelamento via F1)."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Callable

import requests

from . import config


class LLMError(RuntimeError):
    """Erro amigável para exibir no terminal."""


class Cancelled(RuntimeError):
    """Geração interrompida pelo usuário (F1 / /kill / Ctrl+C)."""


def chat(model: str, messages: list[dict], *, fmt: str | None = None,
         options: dict | None = None, timeout: float | None = None,
         cancel: threading.Event | None = None,
         on_token: Callable[[int], None] | None = None,
         think: bool | None = None) -> dict:
    """Chat com o Ollama. Retorna o texto completo + métricas de desempenho.

    Usa streaming internamente: a cada token verifica `cancel`; se estiver setado, fecha a
    conexão (o Ollama interrompe a geração na GPU) e levanta Cancelled.

    Importante: não enviamos mensagens 'system' — assim o SYSTEM do Modelfile
    (persona do Jarvis/Friday) é sempre preservado pelo Ollama.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
    }
    if fmt:
        payload["format"] = fmt
    if options:
        payload["options"] = options
    if think is not None:                 # modelos de raciocínio (deepseek-r1): liga/desliga o <think>
        payload["think"] = think

    if cancel is not None and cancel.is_set():
        raise Cancelled()
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, stream=True,
                          timeout=(5, timeout or config.LLM_TIMEOUT))
    except requests.ConnectionError as e:
        raise LLMError(f"Ollama offline em {config.OLLAMA_URL}. Abra o Ollama ou rode `ollama serve`.") from e
    except requests.Timeout as e:
        raise LLMError(f"Timeout aguardando o modelo '{model}'.") from e

    content, thinking, n, final = [], [], 0, {}
    try:
        if r.status_code == 404:
            raise LLMError(f"Modelo '{model}' não encontrado no Ollama. Rode o setup.ps1 (ollama create).")
        if not r.ok:
            raise LLMError(f"Ollama respondeu {r.status_code}: {r.text[:300]}")
        for line in r.iter_lines():
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("error"):
                raise LLMError(f"Ollama: {chunk['error']}")
            msg = chunk.get("message") or {}
            if msg.get("content"):
                content.append(msg["content"])
                n += 1
                if on_token:
                    on_token(n)
            if msg.get("thinking"):
                thinking.append(msg["thinking"])
            if chunk.get("done"):
                final = chunk
                break
    except requests.RequestException as e:
        if cancel is not None and cancel.is_set():
            raise Cancelled() from e
        raise LLMError(f"Conexão com o Ollama interrompida: {e}") from e
    finally:
        r.close()

    eval_count = final.get("eval_count", n) or n
    eval_ns = final.get("eval_duration", 0) or 0
    return {
        "content": "".join(content),
        "thinking": "".join(thinking),
        "seconds": time.perf_counter() - t0,
        "tokens": eval_count,
        "tps": eval_count / (eval_ns / 1e9) if eval_ns else 0.0,
    }


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_think(text: str) -> str:
    """Remove o bloco de raciocínio <think> dos modelos R1."""
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def strip_telemetry(text: str) -> str:
    """Remove a assinatura '💡 Telemetria do Jarvis' (para textos que vão para fora)."""
    idx = text.find("💡 Telemetria")
    return (text[:idx] if idx >= 0 else text).strip()


# aceita variações que o modelo inventa ("Telemetria do Plato", "**Telemetria da Monday**:")
_TELEMETRY_RE = re.compile(r"💡\s*\**\s*Telemetria d[oa] \w+\s*:?\**\s*:?[ \t]*([^\n]*)", re.I)


def telemetry_comment(text: str) -> str:
    """Comentário que o modelo escreveu após a marca — só se a marca estiver no fim da resposta
    (quando o 7b a coloca no início, o 'comentário' é na verdade a primeira frase da resposta)."""
    text = text or ""
    for m in reversed(list(_TELEMETRY_RE.finditer(text))):
        c = m.group(1).strip(" *_")
        if c and m.start() >= len(text) * 0.6:
            return c[:240]
    return ""


def strip_telemetry_all(text: str) -> str:
    """Remove todas as ocorrências da marca (o modelo 7b às vezes a põe no início ou duplica)."""
    return re.sub(r"\n{3,}", "\n\n", _TELEMETRY_RE.sub("", text or "")).strip()


def is_online(timeout: float = 2.0) -> bool:
    try:
        return requests.get(f"{config.OLLAMA_URL}/api/version", timeout=timeout).ok
    except requests.RequestException:
        return False


def installed_models() -> list[str]:
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def loaded_models() -> list[dict]:
    """Modelos atualmente carregados na VRAM (ollama ps)."""
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/ps", timeout=2)
        return [{"name": m.get("name"), "vram_gb": round((m.get("size_vram") or 0) / 1024**3, 2)}
                for m in r.json().get("models", [])]
    except Exception:
        return []


def has_model(name: str, installed: list[str] | None = None) -> bool:
    installed = installed if installed is not None else installed_models()
    base = name if ":" in name else f"{name}:latest"
    return name in installed or base in installed
