"""MONDAY — automação do WhatsApp Web (pywhatkit).

Requisitos: WhatsApp Web já logado no navegador padrão e o PC desbloqueado
(o pywhatkit abre a conversa e usa o teclado para enviar).

Política de segurança: mensagens REDIGIDAS pela IA ficam pendentes até o usuário
confirmar com /send. Só o comando /whatsapp (texto literal do usuário) envia direto.
"""
from __future__ import annotations

import json
import logging
import re
import threading
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


def load_contacts() -> dict[str, str]:
    if config.CONTACTS_FILE.exists():
        try:
            return {k.lower(): v for k, v in json.loads(config.CONTACTS_FILE.read_text(encoding="utf-8")).items()}
        except Exception as e:
            log.warning("contacts.json inválido: %s", e)
    return {}


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
    contacts = load_contacts()
    if target.lower() in contacts:
        return normalize_phone(contacts[target.lower()]), target
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
