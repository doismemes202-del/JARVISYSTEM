"""FRIDAY — Auditora Chefe de Qualidade (QA/QC).

Toda saída técnica passa por aqui antes de chegar ao usuário. A Friday:
  - aprova  → responde [APROVADO PARA SAÍDA]  → selo PASSED
  - reprova → devolve a versão corrigida       → selo CORRECTED
  - offline → saída original com selo SKIPPED (o sistema não trava)
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from . import config, llm

log = logging.getLogger("jarvis.friday")

APPROVED_TAG = "[APROVADO PARA SAÍDA]"
BADGE_PASSED = "🛡️ Revisado e Aprovado pela Friday [QA Status: PASSED]"
BADGE_CORRECTED = "🛡️ Corrigido pela Friday [QA Status: CORRECTED]"
BADGE_FLAGGED = "⚠️ Friday apontou ressalvas [QA Status: FLAGGED]"
BADGE_SKIPPED = "⚠️ Friday indisponível [QA Status: SKIPPED]"
BADGE_FAST = "⚡ Resposta rápida — conversa simples, sem auditoria [QA Status: FAST]"
DEEP_KINDS = {"codigo", "fusion", "calculo", "bom"}

KIND_GUIDE = {
    "codigo": "código Python: sintaxe, bugs, tratamento de erros, segurança, imports faltando",
    "fusion": "script da API do Fusion 360: uso correto de adsk.core/adsk.fusion, unidades (a API usa cm), "
              "geometria, tolerâncias e se roda dentro de run(context)",
    "bom": "lista de materiais (BOM): quantidades, unidades, preços unitários plausíveis, somas e itens faltando. "
           "Mantenha exatamente o mesmo formato JSON na versão corrigida",
    "whatsapp": "mensagem de WhatsApp: clareza, tom, ortografia e se atende ao pedido. A versão corrigida deve "
                "conter SOMENTE o texto final da mensagem",
    "calculo": "resposta de matemática/suprimentos: refaça cada conta, confira unidades, arredondamentos "
               "(quantidades de peças sempre para cima) e totais",
    "geral": "resposta técnica: exatidão dos fatos, cálculos, código e coerência com o pedido",
}
FORMAT_RULE = ("A versão corrigida deve manter o MESMO formato, estilo e idioma do artefato original "
               "(texto continua texto, JSON continua JSON), alterando apenas o que estiver errado.")

_CORRECTED_RE = re.compile(r"<<<CORRIGIDO\s*(.*?)\s*CORRIGIDO>>>", re.S)


@dataclass
class ReviewResult:
    status: str                 # PASSED | CORRECTED | FLAGGED | SKIPPED
    text: str                   # conteúdo final a ser entregue
    notes: str = ""             # observações da Friday
    seconds: float = 0.0
    raw: str = field(default="", repr=False)

    @property
    def badge(self) -> str:
        return {"PASSED": BADGE_PASSED, "CORRECTED": BADGE_CORRECTED, "FLAGGED": BADGE_FLAGGED,
                "FAST": BADGE_FAST}.get(self.status, BADGE_SKIPPED)

    @property
    def approved(self) -> bool:
        return self.status in ("PASSED", "CORRECTED")


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


def _clean_corrected(text: str) -> str:
    """Modelos pequenos às vezes ecoam o andaime do prompt dentro da correção — remove."""
    if "--- ARTEFATO A AUDITAR ---" in text:
        text = text.split("--- ARTEFATO A AUDITAR ---", 1)[1]
    text = text.split("--- FIM DO ARTEFATO ---", 1)[0]
    text = re.sub(r"(?m)^\s*Pedido original do usu[aá]rio:.*\n.*\n?", "", text)
    return llm.strip_telemetry_all(text).strip()


def build_prompt(content: str, kind: str, context: str) -> str:
    guide = KIND_GUIDE.get(kind, KIND_GUIDE["geral"])
    return (
        f"AUDITORIA QA/QC — tipo de artefato: {kind.upper()} ({guide}).\n\n"
        f"Pedido original do usuário:\n{context.strip() or '(não informado)'}\n\n"
        f"--- ARTEFATO A AUDITAR ---\n{content}\n--- FIM DO ARTEFATO ---\n\n"
        f"Regras de resposta:\n"
        f"1) Se estiver correto, responda APENAS: {APPROVED_TAG}\n"
        f"2) Se houver falhas, responda na primeira linha: [REPROVADO]\n"
        f"   depois liste as falhas em tópicos curtos,\n"
        f"   e por fim a versão corrigida COMPLETA entre as linhas <<<CORRIGIDO e CORRIGIDO>>>.\n"
        f"3) {FORMAT_RULE}"
    )


def review(content: str, kind: str = "geral", context: str = "", *,
           cancel=None, on_token=None) -> ReviewResult:
    """Auditoria síncrona (chamar via thread). llm.Cancelled propaga para quem chamou."""
    if not config.FRIDAY_ENABLED:
        return ReviewResult("SKIPPED", content, "Friday desativada (FRIDAY_ENABLED=0)")
    msgs = [{"role": "user", "content": build_prompt(content, kind, context)}]
    think = kind in DEEP_KINDS          # raciocínio longo só para código, CAD, cálculos e BOM
    try:
        try:
            res = llm.chat(config.FRIDAY_MODEL, msgs, options={"temperature": 0.2},
                           cancel=cancel, on_token=on_token, think=think)
        except llm.LLMError as e:
            if "think" not in str(e).lower():
                raise
            # modelo sem suporte a 'think' (ex.: qwen2.5-coder) → tenta sem o parâmetro
            res = llm.chat(config.FRIDAY_MODEL, msgs, options={"temperature": 0.2},
                           cancel=cancel, on_token=on_token)
    except llm.LLMError as e:
        log.warning("Friday offline: %s", e)
        return ReviewResult("SKIPPED", content, str(e))

    out = llm.strip_think(res["content"])
    norm = _ascii(out)
    rejected = "[REPROVADO]" in norm

    if not rejected and "APROVADO PARA SAIDA" in norm:
        log.info("Friday: PASSED (%.1fs)", res["seconds"])
        return ReviewResult("PASSED", content, seconds=res["seconds"], raw=out)

    m = _CORRECTED_RE.search(out)
    notes = _CORRECTED_RE.sub("", out).replace("[REPROVADO]", "").strip()
    corrected = _clean_corrected(m.group(1)) if m else ""
    if corrected:
        log.warning("Friday: CORRECTED (%.1fs)", res["seconds"])
        return ReviewResult("CORRECTED", corrected, notes, res["seconds"], out)

    log.warning("Friday: FLAGGED sem versão corrigida (%.1fs)", res["seconds"])
    return ReviewResult("FLAGGED", content, notes or out, res["seconds"], out)
