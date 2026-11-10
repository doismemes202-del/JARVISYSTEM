"""MONDAY — automação do WhatsApp Web (pywhatkit).

Requisitos: WhatsApp Web já logado no navegador padrão e o PC desbloqueado
(o pywhatkit abre a conversa e usa o teclado para enviar).

Política de segurança: mensagens REDIGIDAS pela IA ficam pendentes até o usuário
confirmar com /send (ou dizendo "confirmar" por voz). Envio direto só em dois casos:
  - /whatsapp: texto literal do usuário;
  - /zap!: rascunho da IA, mas SOMENTE para contatos marcados com "auto": true no contacts.json.

contacts.json aceita os dois formatos:
  {"mae": "+5511999998888",
   "fornecedor": {"telefone": "+5511977776666", "auto": true}}
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass

from . import config

log = logging.getLogger("jarvis.monday")
_send_lock = threading.Lock()


@dataclass
class PendingMessage:
    phone: str
    label: str
    text: str


_pending: PendingMessage | None = None


@dataclass
class Contact:
    name: str
    phone: str
    auto: bool = False          # liberado para envio automático (/zap!)


def _key(name: str) -> str:
    """'Mãe' == 'mae' == ' MAE '."""
    return unicodedata.normalize("NFKD", name.strip().lower()).encode("ascii", "ignore").decode()


def load_contact_book() -> dict[str, Contact]:
    if not config.CONTACTS_FILE.exists():
        return {}
    try:
        raw = json.loads(config.CONTACTS_FILE.read_text(encoding="utf-8-sig"))
    except Exception as e:
        log.warning("contacts.json inválido: %s", e)
        return {}
    book = {}
    for name, v in raw.items():
        if name.startswith("_"):                       # chaves de comentário
            continue
        if isinstance(v, dict):
            phone = v.get("telefone") or v.get("phone") or ""
            auto = bool(v.get("auto", False))
        else:
            phone, auto = str(v), False
        if phone:
            book[_key(name)] = Contact(name, phone, auto)
    return book


def load_contacts() -> dict[str, str]:
    return {k: c.phone for k, c in load_contact_book().items()}


def contact_names() -> list[str]:
    return [c.name for c in load_contact_book().values()]


def is_auto(target: str) -> bool:
    c = load_contact_book().get(_key(target))
    return bool(c and c.auto)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"Número inválido: {raw!r}")
    if not raw.strip().startswith("+") and len(digits) <= 11:
        digits = config.WHATSAPP_DEFAULT_COUNTRY + digits
    if not 10 <= len(digits) <= 15:
        raise ValueError(f"Número fora do padrão E.164: {raw!r}")
    return "+" + digits


def resolve(target: str) -> tuple[str, str]:
    """Contato (contacts.json) ou número → (telefone, rótulo)."""
    target = target.strip()
    c = load_contact_book().get(_key(target))
    if c:
        return normalize_phone(c.phone), c.name
    if re.search(r"\d{8,}", re.sub(r"\D", "", target)):
        phone = normalize_phone(target)
        return phone, phone
    raise ValueError(f"Contato '{target}' não encontrado em data/contacts.json e não é um número.")


def send_message(phone: str, text: str) -> None:
    """Envia imediatamente via WhatsApp Web (bloqueante; chamar via to_thread)."""
    import pywhatkit  # import tardio: o pywhatkit faz checagens de rede ao importar
    with _send_lock:
        log.info("Enviando WhatsApp para %s (%d caracteres)...", phone, len(text))
        pywhatkit.sendwhatmsg_instantly(phone_no=phone, message=text,
                                        wait_time=config.WHATSAPP_WAIT, tab_close=True, close_time=4)
        log.info("WhatsApp enviado para %s.", phone)


def set_pending(phone: str, label: str, text: str) -> PendingMessage:
    global _pending
    _pending = PendingMessage(phone, label, text)
    return _pending


def get_pending() -> PendingMessage | None:
    return _pending


def pop_pending() -> PendingMessage | None:
    global _pending
    p, _pending = _pending, None
    return p


def format_bom_report(project: str, items: list[dict], url: str | None = None) -> str:
    from .tool_sheets import bom_total, brl
    lines = [f"*📦 BOM — {project}*", ""]
    for n, i in enumerate(items, 1):
        lines.append(f"{n}. {i['componente']} — {i['qtd']:g} {i['unidade']} × {brl(i['preco_unit'])}")
    lines += ["", f"*Total estimado: {brl(bom_total(items))}*"]
    if url:
        lines += ["", f"Planilha: {url}"]
    lines += ["", "_Enviado pelo J.A.R.V.I.S._"]
    return "\n".join(lines)
