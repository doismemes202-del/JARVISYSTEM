"""Configuração central — lê .env (sem dependências externas) e variáveis de ambiente."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():   # tolera BOM do PowerShell
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(ENV_FILE)


def _get(key: str, default: str = "") -> str:
    value = os.environ.get(key, "")
    return value if value != "" else default


def _flag(key: str, default: bool) -> bool:
    return _get(key, "1" if default else "0").lower() in ("1", "true", "yes", "on", "sim")


def _path(key: str, default: str) -> Path:
    p = Path(_get(key, default)).expanduser()
    return p if p.is_absolute() else BASE_DIR / p


def _ensure_token() -> str:
    token = _get("JARVIS_TOKEN")
    if token:
        return token
    token = secrets.token_urlsafe(18)
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\nJARVIS_TOKEN={token}\n")
    os.environ["JARVIS_TOKEN"] = token
    return token


# --- Servidor ---
HOST = _get("JARVIS_HOST", "0.0.0.0")
PORT = int(_get("JARVIS_PORT", "8000"))
TOKEN = _ensure_token()
TEMPLATES_DIR = BASE_DIR / "templates"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# --- Ollama ---
OLLAMA_URL = _get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
JARVIS_MODEL = _get("JARVIS_MODEL", "jarvis")
FRIDAY_MODEL = _get("FRIDAY_MODEL", "friday")
VISION_MODEL = _get("VISION_MODEL", "qwen2.5vl:7b")
OLLAMA_KEEP_ALIVE = _get("OLLAMA_KEEP_ALIVE", "30m")
LLM_TIMEOUT = float(_get("LLM_TIMEOUT", "600"))
FRIDAY_ENABLED = _flag("FRIDAY_ENABLED", True)
# smart  = conversa simples sai direto (⚡ FAST); código, CAD, cálculos e BOM sempre auditados
# always = audita absolutamente tudo (mais lento)
FRIDAY_MODE = _get("FRIDAY_MODE", "smart").lower()
# FRIDAY_DEEP=1: raciocínio longo também ao revisar código (mais lento, ~80-100 s por resposta)
FRIDAY_DEEP = _flag("FRIDAY_DEEP", False)

# --- Kill switch ---
KILL_HOTKEY = _get("KILL_HOTKEY", "f1")
KILL_HOTKEY_ENABLED = _flag("KILL_HOTKEY_ENABLED", True)
KILL_OLLAMA = _flag("KILL_OLLAMA", True)
KILL_DRY_RUN = _flag("JARVIS_KILL_DRY_RUN", False)

# --- Voz ---
VOICE_ENABLED = _flag("VOICE_ENABLED", True)
WAKEWORD_MODEL = _get("WAKEWORD_MODEL", "hey_jarvis")
WAKEWORD_THRESHOLD = float(_get("WAKEWORD_THRESHOLD", "0.35"))   # 0.35 tolera sotaque PT-BR
WHISPER_MODEL = _get("WHISPER_MODEL", "small")
WHISPER_DEVICE = _get("WHISPER_DEVICE", "cuda")
WHISPER_LANGUAGE = _get("WHISPER_LANGUAGE", "pt")
PIPER_VOICE = _path("PIPER_VOICE", "voices/pt_BR-faber-medium.onnx")
PIPER_EXE = _get("PIPER_EXE")

# --- Google Sheets ---
GOOGLE_CREDENTIALS = _path("GOOGLE_CREDENTIALS", "credentials/service_account.json")
BOM_SPREADSHEET_ID = _get("BOM_SPREADSHEET_ID")
BOM_SPREADSHEET_NAME = _get("BOM_SPREADSHEET_NAME", "JARVIS - BOM")
GOOGLE_SHARE_EMAIL = _get("GOOGLE_SHARE_EMAIL")

# --- WhatsApp ---
WHATSAPP_WAIT = int(_get("WHATSAPP_WAIT", "20"))
WHATSAPP_DEFAULT_COUNTRY = _get("WHATSAPP_DEFAULT_COUNTRY", "55")
CONTACTS_FILE = DATA_DIR / "contacts.json"

# --- Fusion 360 / Open Interpreter ---
FUSION_SCRIPTS_DIR = _path(
    "FUSION_SCRIPTS_DIR",
    str(Path(os.environ.get("APPDATA", str(Path.home()))) / "Autodesk" / "Autodesk Fusion 360" / "API" / "Scripts"),
)
OI_PYTHON = _get("OI_PYTHON")
