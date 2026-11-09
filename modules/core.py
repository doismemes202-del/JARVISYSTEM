"""Núcleo de orquestração — compartilhado pela CLI (jarvis_cli.py) e pelo servidor web.

Engine.handle(texto, progress) é síncrono e cancelável:
  - progress(agente, ação) é chamado a cada etapa (o front-end mostra spinner/badges);
  - Engine.abort() (F1, /kill, Ctrl+C) interrompe a geração em andamento → llm.Cancelled.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import config, llm, telemetry, tool_fusion, tool_reviewer, tool_sheets, tool_whatsapp

log = logging.getLogger("jarvis.core")

Progress = Callable[[str, str], None]


def _noop(agent: str, action: str) -> None:
    pass


# =============================================================================== AGENTES
AGENTS: dict[str, dict] = {
    "jarvis": {"label": "JARVIS", "icon": "🤖", "color": "#00e5ff", "role": "Orquestrador · Voz · Interface principal"},
    "monday": {"label": "MONDAY", "icon": "💼", "color": "#b46cff", "role": "Secretária · WhatsApp · Agenda · Rotina"},
    "romeu": {"label": "ROMEU", "icon": "💻", "color": "#39ff88", "role": "Programador Sênior · Python · APIs · Scripts"},
    "papa": {"label": "PAPA", "icon": "⚙️", "color": "#ff9f1c", "role": "Engenheiro CAD/CAM · Fusion 360 · 3D"},
    "plato": {"label": "PLATO", "icon": "📊", "color": "#ffd60a", "role": "Matemática · Suprimentos · BOM no Google Sheets"},
    "friday": {"label": "FRIDAY", "icon": "🛡️", "color": "#3d8bff", "role": "Auditora Chefe de Qualidade (QA/QC)"},
}

ROLES: dict[str, dict] = {
    "jarvis": {"kind": "geral", "directive": "", "kw": []},
    "monday": {
        "kind": "geral",
        "directive": "[SUBSISTEMA MONDAY ATIVO] Responda como MONDAY, a secretária do laboratório "
                     "(WhatsApp, agenda, lembretes, notificações e rotina). Organizada, objetiva e cordial.",
        "kw": ["whatsapp", "zap", "mensage", "agenda", "lembre", "reuniao", "compromisso", "notific",
               "rotina", "avis", "horario", "e-mail", "email"]},
    "romeu": {
        "kind": "codigo",
        "directive": "[SUBSISTEMA ROMEU ATIVO] Responda como ROMEU, programador sênior Python. Código "
                     "completo, tipado, com tratamento de erros e instruções de execução no Windows/PowerShell.",
        "kw": ["python", "codigo", "script", "api", "funcao", "classe", "bug", "erro", "exception",
               "traceback", "biblioteca", "fastapi", "json", "regex", "programa", "automatiz", "pip"]},
    "papa": {
        "kind": "fusion",
        "directive": "[SUBSISTEMA PAPA ATIVO] Responda como PAPA, engenheiro CAD/CAM especialista em "
                     "Autodesk Fusion 360, modelagem 3D, tolerâncias, usinagem e impressão 3D.",
        "kw": ["fusion", "cad", "cam", "3d", "peca", "extrus", "sketch", "esboco", "stl", "step",
               "toleranc", "usinag", "cnc", "fresa", "chanfro", "filete", "montagem", "modelag", "impressao"]},
    "plato": {
        "kind": "calculo",
        "directive": "[SUBSISTEMA PLATO ATIVO] Responda como PLATO, matemático e gestor de suprimentos. "
                     "Mostre cálculos passo a passo, unidades e totais. Valores monetários em R$.",
        "kw": ["calcul", "bom", "material", "lista de", "custo", "orcament", "planilha", "sheets",
               "matemat", "equac", "quantidade", "quantos", "quantas", "preco", "fornecedor", "suprimento",
               "converter", "estoque", "parafuso", "porcentagem", "reserva", "media"]},
}
# palavras curtas exigem palavra inteira ("cad" não pode casar com "cada"); as longas casam por prefixo
_KW_RE = {r: [re.compile(rf"\b{re.escape(k)}\b" if len(k) <= 3 else rf"\b{re.escape(k)}") for k in v["kw"]]
          for r, v in ROLES.items()}

KIND_ACTION = {"codigo": "Auditando código", "fusion": "Auditando script do Fusion 360",
               "bom": "Auditando BOM e preços", "whatsapp": "Auditando mensagem",
               "calculo": "Conferindo cálculos", "geral": "Auditando resposta"}


def norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()


def route(text: str) -> str:
    t = norm(text)
    for role in ROLES:                                     # chamada direta: "romeu, ..." / "plato: ..."
        if re.match(rf"\s*(ei\s+|hey\s+|ok\s+)?{role}\b", t):
            return role
    scores = {r: sum(1 for rx in rxs if rx.search(t)) for r, rxs in _KW_RE.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "jarvis"


def is_kill(text: str) -> bool:
    words = re.findall(r"[a-z/]+", norm(text))
    return 0 < len(words) <= 3 and any(w.strip("/") in ("kill", "abort", "abortar") for w in words)


HELP = """\
| Comando | Ação |
|---|---|
| `<texto livre>` | JARVIS roteia para MONDAY · ROMEU · PAPA · PLATO |
| `/agent <nome> <texto>` | força um subsistema |
| `/bom <projeto> \\| <descrição>` | PLATO gera BOM cotada → FRIDAY → Google Sheets |
| `/zapbom <contato\\|+55...>` | prepara o relatório da última BOM para WhatsApp |
| `/zap <contato\\|+55...> \\| <pedido>` | MONDAY redige → FRIDAY revisa → aguarda `/send` |
| `/send` · `/cancel` | confirma ou descarta a mensagem pendente |
| `/whatsapp <+55...> <texto>` | envia o texto literal imediatamente |
| `/fusion placa c=100 l=60 e=5 furo=5 margem=8` | script parametrizado (template) |
| `/fusion <descrição>` | PAPA escreve script Fusion 360 → FRIDAY → pasta Scripts |
| `/os <instrução>` | Open Interpreter `--os` com visão local em console próprio |
| `/status` · `/agents` · `/clear` · `/voice on\\|off` | telemetria · agentes · memória · voz |
| `/kill` · **F1** · `kill` | ☠ PARADA DE EMERGÊNCIA (cancela tudo → IDLE) |
| `/shutdown` | encerra JARVIS, Ollama e ferramentas |"""


@dataclass
class Reply:
    agent: str
    body: str
    qa: str = ""                 # PASSED | CORRECTED | FLAGGED | SKIPPED | ""
    badge: str = ""
    telemetry: str = ""          # linha '💡 Telemetria do Jarvis: ...'
    notes: str = ""              # ressalvas da Friday
    meta: str = ""               # métricas de desempenho
    extra: dict = field(default_factory=dict)

    def as_text(self) -> str:
        parts = [self.body.strip()]
        if self.telemetry:
            parts.append(self.telemetry)
        if self.notes:
            parts.append(f"⚠️ Ressalvas da Friday:\n{self.notes.strip()[:1200]}")
        if self.badge:
            parts.append(self.badge)
        return "\n\n".join(p for p in parts if p)


# =============================================================================== DIRETIVAS
BOM_DIRECTIVE = (
    "[SUBSISTEMA PLATO — MODO BOM] Gere a lista de materiais cotada para o projeto abaixo. "
    "Responda SOMENTE com JSON no formato "
    '{"itens":[{"componente":"","especificacao":"","qtd":1,"unidade":"un","preco_unit":0.0,'
    '"fornecedor":"","link":"","obs":""}]}. Preços estimados em reais (R$) no mercado brasileiro.')

ZAP_DIRECTIVE = (
    "[SUBSISTEMA MONDAY — REDAÇÃO DE WHATSAPP] Escreva APENAS o texto final da mensagem de WhatsApp "
    "pedida abaixo, pronto para envio, em português, sem comentários, sem aspas e sem a telemetria. "
    "Destinatário: {dest}.\nPedido: {ask}")

PAPA_SCRIPT_DIRECTIVE = (
    "[SUBSISTEMA PAPA — SCRIPT FUSION 360] Escreva um script Python COMPLETO para a API do Autodesk Fusion 360 "
    "que execute o pedido abaixo. Requisitos: `import adsk.core, adsk.fusion, traceback`; função "
    "`run(context)`; obter design ativo via adsk.fusion.Design.cast(app.activeProduct); criar User Parameters "
    "para as dimensões; lembrar que a API usa centímetros; try/except com ui.messageBox(traceback). "
    "Entregue o código em um único bloco ```python.\nPedido: {ask}")


# =============================================================================== ENGINE
class Engine:
    def __init__(self) -> None:
        self.memory: deque[dict] = deque(maxlen=16)
        self.lock = threading.Lock()            # uma inferência por vez na GPU
        self.cancel = threading.Event()
        self.last_bom: dict | None = None

    # ------------------------------------------------------------------ controle
    def abort(self) -> None:
        self.cancel.set()

    def busy(self) -> bool:
        return self.lock.locked()

    def handle(self, text: str, progress: Progress = _noop) -> Reply:
        text = (text or "").strip()
        if not text:
            raise ValueError("Comando vazio.")
        with self.lock:
            self.cancel.clear()              # limpa só depois de obter a GPU (F1 antigo não vaza)
            if text.startswith("/"):
                return self._slash(text, progress)
            return self.converse(text, progress)

    # ------------------------------------------------------------------ helpers
    def _llm(self, agent: str, prompt: str, progress: Progress, action: str,
             history: bool = False, **kw) -> dict:
        label = AGENTS[agent]["label"]
        progress(agent, f"[{label}] {action}")
        msgs = [*self.memory] if history else []
        msgs.append({"role": "user", "content": prompt})
        return llm.chat(config.JARVIS_MODEL, msgs, cancel=self.cancel,
                        on_token=lambda n: progress(agent, f"[{label}] {action} · {n} tokens"), **kw)

    def _friday(self, content: str, kind: str, context: str, progress: Progress) -> tool_reviewer.ReviewResult:
        if not config.FRIDAY_ENABLED:
            return tool_reviewer.ReviewResult("SKIPPED", content, "desativada")
        action = KIND_ACTION.get(kind, "Auditando")
        progress("friday", f"[FRIDAY] {action}")
        rr = tool_reviewer.review(content, kind, context, cancel=self.cancel,
                                  on_token=lambda n: progress("friday", f"[FRIDAY] {action} · {n} tokens"))
        if rr.status in ("CORRECTED", "FLAGGED") and rr.notes:
            log.info("FRIDAY: %s", rr.notes[:400])
        return rr

    @staticmethod
    def _perf(label: str, res: dict, rr: tool_reviewer.ReviewResult | None = None) -> str:
        s = f"{label} {res['seconds']:.1f}s · {res['tps']:.0f} tok/s"
        if rr and rr.seconds:
            s += f" │ FRIDAY {rr.seconds:.1f}s"
        return s

    @staticmethod
    def _signature(raw: str, label: str, res: dict) -> str:
        """Marca '💡 Telemetria do Jarvis:' uma única vez, no final, com métricas reais."""
        comment = llm.telemetry_comment(raw)
        g = telemetry.gpu().read()
        vram = f" · VRAM {g['vram_used_gb']}/{g['vram_total_gb']} GB" if g else ""
        stats = f"[{label} · {res['seconds']:.1f}s · {res['tps']:.0f} tok/s{vram}]"
        return f"💡 Telemetria do Jarvis: {comment + ' ' if comment else ''}{stats}"

    # ------------------------------------------------------------------ conversa
    def converse(self, text: str, progress: Progress = _noop, role: str | None = None) -> Reply:
        role = role or route(text)
        info, label = ROLES[role], AGENTS[role]["label"]
        log.info("Roteado → %s", label)
        content = f"{info['directive']}\n\n{text}" if info["directive"] else text
        res = self._llm(role, content, progress, "Gerando resposta", history=True)
        raw = llm.strip_think(res["content"])
        rr = self._friday(llm.strip_telemetry_all(raw), info["kind"], text, progress)
        self.memory.extend([{"role": "user", "content": text}, {"role": "assistant", "content": rr.text}])
        return Reply(role, rr.text, rr.status, rr.badge, self._signature(raw, label, res),
                     rr.notes if rr.status == "FLAGGED" else "", self._perf(label, res, rr))

    # ------------------------------------------------------------------ comandos
    def _slash(self, text: str, progress: Progress) -> Reply:
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        handler = {
            "/help": lambda: Reply("jarvis", HELP),
            "/clear": self._clear,
            "/agent": lambda: self._agent(arg, progress),
            "/bom": lambda: self.bom(arg, progress),
            "/zapbom": lambda: self.zapbom(arg),
            "/zap": lambda: self.zap(arg, progress),
            "/send": lambda: self.send(progress),
            "/cancel": self.cancel_pending,
            "/whatsapp": lambda: self.whatsapp(arg, progress),
            "/fusion": lambda: self.fusion(arg, progress),
            "/os": lambda: self.open_interpreter(arg),
        }.get(cmd)
        if not handler:
            raise ValueError(f"Comando desconhecido: {cmd}. Digite /help.")
        return handler()

    def _clear(self) -> Reply:
        self.memory.clear()
        return Reply("jarvis", "Memória de conversa apagada. Começando do zero — de novo.")

    def _agent(self, arg: str, progress: Progress) -> Reply:
        name, _, rest = arg.partition(" ")
        if name.lower() not in ROLES or not rest:
            raise ValueError("Uso: /agent <jarvis|monday|romeu|papa|plato> <texto>")
        return self.converse(rest, progress, name.lower())

    def bom(self, arg: str, progress: Progress) -> Reply:
        project, _, desc = arg.partition("|")
        project, desc = project.strip(), (desc or project).strip()
        if not project:
            raise ValueError("Uso: /bom <projeto> | <descrição>")
        res = self._llm("plato", f"{BOM_DIRECTIVE}\n\nProjeto: {desc}", progress, "Cotando materiais", fmt="json")
        items = tool_sheets.parse_bom_json(res["content"])
        rr = self._friday(json.dumps({"itens": items}, ensure_ascii=False, indent=1), "bom", desc, progress)
        if rr.status == "CORRECTED":
            try:
                items = tool_sheets.parse_bom_json(rr.text)
            except ValueError:
                log.warning("Correção da Friday não é JSON válido — mantendo a BOM original.")
        sheet_msg, url = "", None
        progress("plato", "[PLATO] Gravando no Google Sheets")
        try:
            url = tool_sheets.save_bom(project, items)
            sheet_msg = f"📊 Google Sheets: {url}"
        except tool_sheets.SheetsNotConfigured as e:
            sheet_msg = f"⚠️ Sheets não configurado: {e}"
        except Exception as e:
            log.exception("Falha no Google Sheets")
            sheet_msg = f"⚠️ Falha ao gravar no Sheets: {e}"
        self.last_bom = {"project": project, "items": items, "url": url}
        body = f"**BOM — {project}**\n\n```text\n{tool_sheets.format_bom_table(items)}\n```\n\n{sheet_msg}"
        return Reply("plato", body, rr.status, rr.badge, notes=rr.notes if rr.status == "FLAGGED" else "",
                     meta=self._perf("PLATO", res, rr),
                     extra={"bom": items, "project": project, "url": url, "sheet_msg": sheet_msg})

    def zapbom(self, arg: str) -> Reply:
        if not self.last_bom:
            raise ValueError("Nenhuma BOM gerada nesta sessão. Use /bom primeiro.")
        phone, label = tool_whatsapp.resolve(arg)
        msg = tool_whatsapp.format_bom_report(self.last_bom["project"], self.last_bom["items"], self.last_bom["url"])
        tool_whatsapp.set_pending(phone, label, msg)
        return Reply("monday", f"Relatório pronto para **{label}** ({phone}):\n\n```text\n{msg}\n```\n\n"
                               f"Digite `/send` para enviar ou `/cancel`.", extra={"pending": True})

    def zap(self, arg: str, progress: Progress) -> Reply:
        dest, _, ask = arg.partition("|")
        if not ask.strip():
            raise ValueError("Uso: /zap <contato|+55...> | <o que dizer>")
        phone, label = tool_whatsapp.resolve(dest)
        res = self._llm("monday", ZAP_DIRECTIVE.format(dest=label, ask=ask.strip()), progress, "Redigindo mensagem")
        draft = llm.strip_telemetry_all(llm.strip_think(res["content"])).strip('"')
        rr = self._friday(draft, "whatsapp", ask, progress)
        final = llm.strip_telemetry_all(rr.text).strip('"')
        tool_whatsapp.set_pending(phone, label, final)
        return Reply("monday", f"Rascunho para **{label}** ({phone}):\n\n```text\n{final}\n```\n\n"
                               f"Digite `/send` para enviar ou `/cancel`.",
                     rr.status, rr.badge, meta=self._perf("MONDAY", res, rr), extra={"pending": True})

    def send(self, progress: Progress) -> Reply:
        p = tool_whatsapp.pop_pending()
        if not p:
            raise ValueError("Nenhuma mensagem pendente.")
        progress("monday", f"[MONDAY] Enviando WhatsApp para {p.label}")
        tool_whatsapp.send_message(p.phone, p.text)
        return Reply("monday", f"✅ Mensagem enviada para **{p.label}** ({p.phone}).")

    def cancel_pending(self) -> Reply:
        p = tool_whatsapp.pop_pending()
        return Reply("monday", "Mensagem descartada." if p else "Nada pendente.")

    def whatsapp(self, arg: str, progress: Progress) -> Reply:
        dest, _, body = arg.partition(" ")
        if not body.strip():
            raise ValueError("Uso: /whatsapp <+55...|contato> <texto>")
        phone, label = tool_whatsapp.resolve(dest)
        progress("monday", f"[MONDAY] Enviando WhatsApp para {label}")
        tool_whatsapp.send_message(phone, body.strip())
        return Reply("monday", f"✅ Enviado para **{label}** ({phone}).")

    def fusion(self, arg: str, progress: Progress) -> Reply:
        if not arg:
            raise ValueError("Uso: /fusion placa c=100 l=60 e=5   ou   /fusion <descrição da peça>")
        if norm(arg).startswith("placa"):
            progress("papa", "[PAPA] Gerando placa parametrizada")
            path, params = tool_fusion.generate_plate(arg)
            plist = "\n".join(f"| `{k}` | {v[0]:g} {v[1]} |" for k, v in params.items())
            return Reply("papa", f"Script parametrizado gerado:\n`{path}`\n\n| Parâmetro | Valor |\n|---|---|\n"
                                 f"{plist}\n\nNo Fusion: **Shift+S → Scripts → {path.stem} → Run**.")
        res = self._llm("papa", PAPA_SCRIPT_DIRECTIVE.format(ask=arg), progress, "Gerando script do Fusion 360")
        code = tool_fusion.extract_code(llm.strip_think(res["content"]))
        problems = tool_fusion.validate_fusion_script(code)
        ctx = arg + ("\nProblemas detectados automaticamente: " + "; ".join(problems) if problems else "")
        rr = self._friday(f"```python\n{code}\n```", "fusion", ctx, progress)
        if rr.status == "CORRECTED":
            code = tool_fusion.extract_code(rr.text)
        problems = tool_fusion.validate_fusion_script(code)
        name = f"jarvis_{tool_fusion.slug(arg, 24)}_{datetime.now():%H%M%S}"
        path = tool_fusion.save_script(name, code, arg)
        warn = ("\n\n⚠️ Validação: " + "; ".join(problems)) if problems else ""
        return Reply("papa", f"```python\n{code}\n```\n\nSalvo em: `{path}`\n\n"
                             f"No Fusion: **Shift+S → Scripts → {name} → Run**.{warn}",
                     rr.status, rr.badge, notes=rr.notes if rr.status == "FLAGGED" else "",
                     meta=self._perf("PAPA", res, rr))

    def open_interpreter(self, arg: str) -> Reply:
        if not arg:
            raise ValueError("Uso: /os <instrução para o Open Interpreter>")
        proc = tool_fusion.launch_open_interpreter(arg)
        return Reply("papa", f"Open Interpreter em modo OS aberto (PID {proc.pid}) com `{config.VISION_MODEL}`.\n\n"
                             f"Acompanhe e confirme as ações na janela do console. **F1** encerra tudo.")
