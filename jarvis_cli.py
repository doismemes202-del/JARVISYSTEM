"""
THE JARVIS SYSTEM — interface de terminal (CLI) para o PowerShell.

    python jarvis_cli.py                 # modo interativo
    python jarvis_cli.py --web           # + terminal web para o celular (porta 8000)
    python jarvis_cli.py --once "texto"  # executa um comando e sai (repetível)

F1 em qualquer janela = PARADA DE EMERGÊNCIA: cancela a geração, encerra Open Interpreter e
automações e volta ao estado IDLE.  /shutdown encerra tudo (inclusive o Ollama).
"""
from __future__ import annotations

import argparse
import functools
import logging
import os
import queue
import re
import sys
import threading
import time

if os.name == "nt":                       # UTF-8 + sequências ANSI no console do Windows
    os.system("")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from modules import config, core, kill_switch, llm, telemetry, tool_whatsapp

console = Console(highlight=False)
log = logging.getLogger("jarvis")

QA_STYLE = {
    "PASSED": ("🛡️ QA Status: PASSED", "bold #39ff88"),
    "CORRECTED": ("🛡️ QA Status: CORRECTED — corrigido pela Friday", "bold #ffd60a"),
    "FLAGGED": ("⚠️ QA Status: FLAGGED — ver ressalvas", "bold #ff9f1c"),
    "SKIPPED": ("⚠️ QA Status: SKIPPED — Friday indisponível", "dim"),
    "FAST": ("⚡ Resposta rápida — conversa simples", "bold #00e5ff"),
}
LOCAL_COMMANDS = ["/status", "/agents", "/kill", "/shutdown", "/voice on", "/voice off", "/voice test",
                  "/cls", "/exit"]
ENGINE_COMMANDS = ["/help", "/clear", "/agent ", "/bom ", "/zapbom ", "/zap ", "/send", "/cancel",
                   "/whatsapp ", "/fusion placa c=100 l=60 e=5 furo=5 margem=8", "/fusion ", "/os "]


# =============================================================================== BANNER
REACTOR = [
    "      ▄▄▄████▄▄▄      ",
    "   ▄██▀▀  ▄▄  ▀▀██▄   ",
    "  ██▀  ▄██████▄  ▀██  ",
    " ██   ██▀ ▄▄ ▀██   ██ ",
    " ██   ██▄ ▀▀ ▄██   ██ ",
    "  ██▄  ▀██████▀  ▄██  ",
    "   ▀██▄▄  ▀▀  ▄▄██▀   ",
    "      ▀▀▀████▀▀▀      ",
]
WORDMARK = [
    "     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗",
    "     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝",
    "     ██║███████║██████╔╝██║   ██║██║███████╗",
    "██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║",
    "╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║",
    " ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝",
]


def _lerp(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def gradient(lines: list[str], c1: str = "#00ffea", c2: str = "#2a5bff", vertical: float = 0.25) -> Text:
    """Degradê diagonal ciano → azul neon, caractere a caractere."""
    width = max(len(l) for l in lines) or 1
    out = Text()
    for y, line in enumerate(lines):
        for x, ch in enumerate(line):
            t = min(1.0, x / width * (1 - vertical) + y / max(1, len(lines) - 1) * vertical)
            out.append(ch, style=f"bold {_lerp(c1, c2, t)}" if ch.strip() else "")
        out.append("\n")
    out.rstrip()
    return out


def banner(web_url: str | None = None) -> None:
    logo = Table.grid(padding=(0, 3))
    logo.add_column(vertical="middle")
    logo.add_column(vertical="middle")
    word = gradient(WORDMARK)
    word.append("\n\n   Just A Rather Very Intelligent System", style="italic #5fa8b8")
    logo.add_row(gradient(REACTOR, "#ffffff", "#00b7ff", vertical=0.6), word)
    console.print()
    console.print(Align.center(logo))
    console.print()

    online = llm.is_online()
    installed = llm.installed_models() if online else []
    dot = lambda ok: Text("● ONLINE ", style="bold #39ff88") if ok else Text("○ OFFLINE", style="bold #ff3b5f")

    st = Table.grid(padding=(0, 2))
    st.add_column(style="#5fa8b8", justify="right")
    st.add_column()
    st.add_column(style="dim")
    st.add_row("Ollama", dot(online), config.OLLAMA_URL)
    for label, model in (("jarvis", config.JARVIS_MODEL), ("friday", config.FRIDAY_MODEL),
                         ("visão", config.VISION_MODEL)):
        st.add_row(label, dot(llm.has_model(model, installed)), model)
    gpu = telemetry.gpu()
    st.add_row("GPU", Text(gpu.name if gpu.ok else "N/A", style="bold #00e5ff"), "")
    if web_url:
        st.add_row("Celular", Text("● WEB", style="bold #39ff88"), web_url)

    agents = Text()
    for key, a in core.AGENTS.items():
        agents.append(f" {a['icon']} {a['label']} ", style=f"bold {a['color']}")

    console.print(Panel(
        Group(st, Text(), Align.center(agents)),
        title=Text(" SISTEMAS ", style="bold #00e5ff"), border_style="#0f5f73", box=box.ROUNDED,
        padding=(1, 2)))
    console.print(Text.assemble(
        ("  [F1] ", "bold white on #b0001e"),
        (" Pressione F1 para PARADA DE EMERGÊNCIA (KILL SWITCH)", "bold #ff6b81")))
    console.print(Text("  /help comandos · /agents agentes · /status telemetria · /voice on falar por voz · /exit sair\n",
                       style="dim"))
    if not online:
        console.print(Text("  ⚠ Ollama offline — rode `ollama serve` (ou start_jarvis.ps1).\n", style="#ffd60a"))


# =============================================================================== RENDERIZAÇÃO
_CODE_RE = re.compile(r"```([\w+#.-]*)[ \t]*\n(.*?)```", re.S)
_LANG_ALIAS = {"py": "python", "c++": "cpp", "cxx": "cpp", "js": "javascript", "ps": "powershell",
               "ps1": "powershell", "sh": "bash", "": "text", "txt": "text"}


def _guess_lang(code: str) -> str:
    s = code.lstrip()
    if s[:1] in "{[":
        return "json"
    if re.search(r"^\s*(import |from \w+ import|def |class )", code, re.M):
        return "python"
    if re.search(r"#include\s*<", code):
        return "cpp"
    return "text"


_LATEX = [(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2"), (r"\\text\{([^{}]*)\}", r"\1"),
          (r"\\(?:times|cdot)", "×"), (r"\\approx", "≈"), (r"\\div", "÷"), (r"\\pi", "π"),
          (r"\\sqrt\{([^{}]*)\}", r"√(\1)"), (r"\\%", "%"), (r"\\,|\\;|\\!", " "), (r"\\\$", "$"),
          (r"\\left|\\right", ""), (r"\\\[|\\\]|\\\(|\\\)", "")]


def delatex(text: str) -> str:
    """Fórmulas LaTeX que o modelo escreve viram texto legível no terminal (2400 × 0,15 = 360)."""
    for pat, rep in _LATEX:
        text = re.sub(pat, rep, text)
    return text


def render_body(text: str):
    """Texto → Markdown; blocos ``` → Syntax com destaque e números de linha."""
    parts, pos = [], 0
    for m in _CODE_RE.finditer(text):
        prose = delatex(text[pos:m.start()]).strip()
        if prose:
            parts.append(Markdown(prose, code_theme="monokai"))
        lang = _LANG_ALIAS.get(m.group(1).lower(), m.group(1).lower()) or "text"
        code = m.group(2).rstrip()
        if lang == "text":
            lang = _guess_lang(code)
        parts.append(Panel(Syntax(code, lang, theme="monokai", line_numbers=lang != "text",
                                  word_wrap=True, background_color="#14161c"),
                           title=Text(f" {lang} ", style="dim"), title_align="right",
                           border_style="#2b3240", box=box.ROUNDED, padding=(0, 1)))
        pos = m.end()
    tail = delatex(text[pos:]).strip()
    if tail:
        parts.append(Markdown(tail, code_theme="monokai"))
    return Group(*parts) if parts else Text("")


def bom_table(items: list[dict]) -> Table:
    from modules.tool_sheets import bom_total, brl
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold #ffd60a", border_style="#5c4d00", expand=True)
    for col, just in (("#", "right"), ("Componente", "left"), ("Especificação", "left"), ("Qtd", "right"),
                      ("Un", "left"), ("Unit.", "right"), ("Subtotal", "right"), ("Fornecedor", "left")):
        t.add_column(col, justify=just, overflow="fold")
    for n, i in enumerate(items, 1):
        t.add_row(str(n), i["componente"], i["especificacao"][:40], f"{i['qtd']:g}", i["unidade"],
                  brl(i["preco_unit"]), brl(i["qtd"] * i["preco_unit"]), i["fornecedor"][:22])
    t.add_row("", Text("TOTAL", style="bold"), "", "", "", "", Text(brl(bom_total(items)), style="bold #ffd60a"), "")
    return t


def agent_title(agent: str) -> Text:
    a = core.AGENTS.get(agent, core.AGENTS["jarvis"])
    return Text.assemble((f" {a['icon']} ", ""), (f"[{a['label']}]", f"bold {a['color']}"),
                         (f" {a['role'].split('·')[0].strip()} ", "dim"))


def render_reply(reply: core.Reply) -> None:
    a = core.AGENTS.get(reply.agent, core.AGENTS["jarvis"])
    blocks = []
    if reply.extra.get("bom"):
        blocks += [Text(f"BOM — {reply.extra['project']}", style="bold"), bom_table(reply.extra["bom"]),
                   Text(reply.extra.get("sheet_msg", ""), style="#5fa8b8")]
    else:
        blocks.append(render_body(reply.body))
    if reply.notes:
        f = core.AGENTS["friday"]
        blocks.append(Panel(Markdown(reply.notes.strip()[:1500]), title=agent_title("friday"),
                            title_align="left", border_style=f["color"], box=box.ROUNDED))
    if reply.telemetry:
        blocks.append(Text(reply.telemetry, style="italic #4fb3c8"))

    subtitle = Text()
    if reply.qa in QA_STYLE:
        label, style = QA_STYLE[reply.qa]
        subtitle.append(f" {label} ", style=style)
    if reply.meta:
        subtitle.append(f" ⏱ {reply.meta} ", style="dim")

    console.print(Panel(Group(*blocks), title=agent_title(reply.agent), title_align="left",
                        subtitle=subtitle if subtitle else None, subtitle_align="right",
                        border_style=a["color"], box=box.ROUNDED, padding=(1, 2)))


def render_error(msg: str, title: str = "ERRO") -> None:
    console.print(Panel(Text(msg, style="#ffb3b3"), title=Text(f" ⚠ {title} ", style="bold #ff3b5f"),
                        title_align="left", border_style="#ff3b5f", box=box.ROUNDED))


def bar(pct: float, width: int = 22) -> Text:
    pct = max(0.0, min(100.0, pct or 0))
    color = "#ff3b5f" if pct >= 90 else "#ffd60a" if pct >= 75 else "#00e5ff"
    filled = round(pct / 100 * width)
    return Text.assemble(("█" * filled, color), ("░" * (width - filled), "#24323a"), (f" {pct:5.1f}%", "bold"))


def status_panel() -> None:
    t = telemetry.snapshot()
    tb = Table(box=box.ROUNDED, border_style="#0f5f73", header_style="bold #00e5ff", expand=False)
    tb.add_column("Recurso", style="#5fa8b8")
    tb.add_column("Uso")
    tb.add_column("Detalhe", style="dim")
    tb.add_row("CPU", bar(t["cpu"]), f"{os.cpu_count()} threads")
    tb.add_row("RAM", bar(t["ram_pct"]), f"{t['ram_used_gb']} / {t['ram_total_gb']} GB")
    if "gpu_util" in t:
        tb.add_row("GPU", bar(t["gpu_util"]), t["gpu_name"])
        tb.add_row("VRAM", bar(t["vram_pct"]), f"{t['vram_used_gb']} / {t['vram_total_gb']} GB")
        tb.add_row("Temp · Energia", bar(t["gpu_temp"]),
                   f"{t['gpu_temp']} °C" + (f" · {t['gpu_power_w']:.0f} W" if t.get("gpu_power_w") else ""))

    models = Table(box=box.SIMPLE, header_style="bold #00e5ff")
    models.add_column("Modelo")
    models.add_column("Status")
    models.add_column("VRAM", justify="right")
    online = llm.is_online()
    installed = llm.installed_models() if online else []
    loaded = {m["name"]: m["vram_gb"] for m in (llm.loaded_models() if online else [])}
    for model in (config.JARVIS_MODEL, config.FRIDAY_MODEL, config.VISION_MODEL):
        full = model if ":" in model else f"{model}:latest"
        if full in loaded:
            st = Text("● NA VRAM", style="bold #39ff88")
        elif llm.has_model(model, installed):
            st = Text("○ pronto", style="#5fa8b8")
        else:
            st = Text("✕ ausente", style="bold #ff3b5f")
        models.add_row(model, st, f"{loaded[full]} GB" if full in loaded else "—")

    pend = tool_whatsapp.get_pending()
    extra = Text.assemble(
        ("Ollama ", "dim"), ("online" if online else "OFFLINE", "#39ff88" if online else "#ff3b5f"),
        ("  ·  Kill switch ", "dim"), (config.KILL_HOTKEY.upper() if state.hotkey else "só /kill", "bold"),
        ("  ·  Voz ", "dim"), ("ativa" if state.voice else "desligada", "bold"),
        ("  ·  WhatsApp pendente ", "dim"), (pend.label if pend else "—", "bold"),
        ("  ·  Memória ", "dim"), (f"{len(state.engine.memory) // 2} trocas", "bold"))
    console.print(Panel(Group(tb, models, extra), title=Text(" ⚡ TELEMETRIA ", style="bold #00e5ff"),
                        title_align="left", border_style="#00e5ff", box=box.ROUNDED, padding=(1, 2)))


def agents_panel() -> None:
    t = Table(box=box.ROUNDED, border_style="#0f5f73", header_style="bold #00e5ff")
    t.add_column("Agente")
    t.add_column("Papel")
    t.add_column("Motor", style="dim")
    t.add_column("Gatilhos", style="dim")
    for key, a in core.AGENTS.items():
        motor = config.FRIDAY_MODEL if key == "friday" else config.JARVIS_MODEL
        kw = ", ".join(core.ROLES[key]["kw"][:5]) if key in core.ROLES else "toda saída técnica"
        if key == "jarvis":
            kw = "padrão / roteador"
        t.add_row(Text(f"{a['icon']} [{a['label']}]", style=f"bold {a['color']}"), a["role"], motor, kw)
    console.print(t)
    console.print(Text("  Chame direto: “Romeu, …”  ·  ou force: /agent plato <pergunta>\n", style="dim"))


# =============================================================================== EXECUÇÃO
class State:
    def __init__(self) -> None:
        self.engine = core.Engine()
        self.aborted = threading.Event()
        self.processing = False
        self.hotkey = False
        self.voice = None
        self.voice_queue: queue.Queue[str] = queue.Queue()
        self.session = None


state = State()


def emergency_stop(reason: str) -> None:
    """F1 / /kill — chamado de qualquer thread."""
    state.aborted.set()
    state.engine.abort()
    report = kill_switch.abort(reason)
    if not state.processing:                   # ocioso: avisa acima do prompt
        n = len(report["targets"])
        console.print(Text.assemble(
            ("\n ⛔ PARADA DE EMERGÊNCIA ", "bold white on #b0001e"),
            (f" ({reason}) — {n} processo(s) encerrado(s). Sistema IDLE.", "bold #ff6b81")))


def run(text: str, speak: bool = False) -> None:
    """Executa um comando no Engine com spinner; F1/Ctrl+C cancela e volta ao IDLE."""
    state.aborted.clear()
    box_: dict = {}
    current = {"agent": "jarvis", "action": "[JARVIS] Roteando"}

    def progress(agent: str, action: str) -> None:
        current["agent"], current["action"] = agent, action

    def worker() -> None:
        try:
            box_["reply"] = state.engine.handle(text, progress)
        except BaseException as e:            # noqa: BLE001 — repassado à thread principal
            box_["error"] = e

    th = threading.Thread(target=worker, daemon=True, name="engine")
    t0 = time.time()
    state.processing = True
    if state.engine.busy():
        current["action"] = "[JARVIS] Aguardando a GPU liberar (geração anterior encerrando)"
    th.start()
    try:
        with console.status("", spinner="dots12") as status:
            while th.is_alive() and not state.aborted.is_set():
                a = core.AGENTS.get(current["agent"], core.AGENTS["jarvis"])
                status.update(Text.assemble((f"{a['icon']} ", ""), (current["action"], f"bold {a['color']}"),
                                            (f"…  {time.time() - t0:4.0f}s", "dim"),
                                            ("   F1/Ctrl+C cancela", "#5a6b73")),
                              spinner_style=a["color"])
                th.join(0.1)
    except KeyboardInterrupt:
        state.aborted.set()
        state.engine.abort()
        kill_switch.abort("Ctrl+C")
    finally:
        state.processing = False

    if state.aborted.is_set():
        console.print(Panel(Text("Geração cancelada, ferramentas encerradas. Sistema em IDLE.", style="#ffb3b3"),
                            title=Text(" ⛔ PARADA DE EMERGÊNCIA ", style="bold #ff3b5f"), title_align="left",
                            border_style="#ff3b5f", box=box.ROUNDED))
        return
    err = box_.get("error")
    if isinstance(err, llm.Cancelled):
        render_error("Operação cancelada.", "CANCELADO")
    elif isinstance(err, (ValueError, llm.LLMError)):
        render_error(str(err))
    elif err is not None:
        log.exception("Falha interna", exc_info=err)
        render_error(f"{type(err).__name__}: {err}", "FALHA INTERNA")
    elif "reply" in box_:
        render_reply(box_["reply"])
        if speak and state.voice:
            threading.Thread(target=state.voice.say, args=(box_["reply"].body,), daemon=True).start()


PTT_KEY = "f2"

VOICE_EVENTS = {
    "wake": ("🎙 Hey Jarvis detectado", "bold #39ff88"),
    "listening": ("🔴 Ouvindo… fale o comando (pausa de 1s encerra)", "bold #ff6b81"),
    "heard": ("🗣 Entendi", "bold #39ff88"),
    "nothing": ("🎙 ", "#ffd60a"),
    "error": ("🎙 Erro de voz: ", "bold #ff3b5f"),
}


def voice_event(kind: str, info: str) -> None:
    """Mostra acima do prompt o que o microfone está fazendo."""
    if kind not in VOICE_EVENTS:
        return
    label, style = VOICE_EVENTS[kind]
    if kind == "wake":
        msg = "🎙 F2 — pode falar" if info == "F2" else f"{label} (confiança {info})"
    elif kind == "heard":
        msg = f"{label}: “{info}”"
    else:
        msg = label + info
    console.print(Text(f"  {msg}", style=style))


def toggle_voice(on: bool) -> None:
    if on and not state.voice:
        from modules.voice_engine import VoiceEngine

        def heard(text: str) -> None:
            state.voice_queue.put(text)
            app = state.session.app if state.session else None
            if app and app.is_running:
                app.loop.call_soon_threadsafe(functools.partial(app.exit, result="\x00voice"))

        state.voice = VoiceEngine(heard, on_event=voice_event)
        state.voice.start()
        with console.status("Carregando Whisper + openWakeWord… (≈15 s na primeira vez)",
                            spinner="dots12", spinner_style="#00e5ff"):
            state.voice.ready.wait(120)
        if state.voice.error or not state.voice.is_alive():
            render_error(f"{state.voice.error or 'motor de voz parou'}\n\nRode /voice test para diagnosticar.", "VOZ")
            state.voice = None
        else:
            ptt = ""
            try:                                 # F2 global = falar agora (push-to-talk)
                import keyboard
                state.ptt_handle = keyboard.add_hotkey(PTT_KEY, state.voice.listen_now, suppress=False)
                ptt = " (em qualquer janela)"
            except Exception as e:
                log.warning("F2 global indisponível (%s) — F2 só funciona com o JARVIS em foco.", e)
            console.print(Panel(Text.assemble(
                ("🎙 Voz ativa", "bold #39ff88"), (f"  ·  microfone: {getattr(state.voice, 'mic_name', '?')}\n\n", "dim"),
                ("Opção 1 — mais fácil: ", "bold"), ("aperte F2", "bold #00e5ff"), (ptt, "dim"),
                (", espere o bipe e fale.\n", ""),
                ("Opção 2 — mãos livres: ", "bold"), ("diga “Rêi DJÁR-vis”", "bold #00e5ff"),
                (" — pronúncia americana, J de “Djalma”; o “Jarvis” com J de “janela” não é reconhecido.\n\n", "dim"),
                ("Depois do bipe: fale o comando e fique 1 s em silêncio. ", ""),
                ("Problemas? ", "dim"), ("/voice test", "bold #ffd60a")),
                border_style="#39ff88", box=box.ROUNDED, padding=(0, 2)))
    elif not on and state.voice:
        if getattr(state, "ptt_handle", None) is not None:
            import keyboard
            try:
                keyboard.remove_hotkey(state.ptt_handle)
            except (KeyError, ValueError):
                pass
            state.ptt_handle = None
        state.voice.stop()
        state.voice = None
        console.print(Text("  🎙 Voz desligada.", style="dim"))


def voice_test() -> None:
    """Diagnóstico guiado do microfone + wake word + Whisper."""
    from modules import voice_engine
    secs = 6.0
    console.print(Panel(Text.assemble(
        ("Quando a barra começar, diga em voz normal:\n\n", ""),
        ("   “Hey Jarvis, que horas são?”\n\n", "bold #00e5ff"),
        (f"Gravação de {secs:.0f} segundos. Carregando modelos…", "dim")),
        title=Text(" 🎙 TESTE DE VOZ ", style="bold #39ff88"), title_align="left",
        border_style="#39ff88", box=box.ROUNDED, padding=(1, 2)))
    from rich.live import Live
    view = {"t": 0.0, "rms": 0.0, "wake": 0.0, "best": 0.0}

    def frame() -> Text:
        lvl = min(100.0, view["rms"] / 30)       # ~3000 RMS = fala forte
        return Text.assemble(("  🎙 nível  ", "dim"), bar(lvl, 26), ("   wake ", "dim"),
                             (f"{view['wake']:.2f}", "bold #39ff88" if view["wake"] >= config.WAKEWORD_THRESHOLD else "bold"),
                             (f" (máx {view['best']:.2f})", "dim"), (f"   {view['t']:.1f}/{secs:.0f}s", "dim"))

    def tick(t: float, rms: float, wake: float) -> None:
        view.update(t=t, rms=rms, wake=wake, best=max(view["best"], wake))
        live.update(frame())

    try:
        with Live(Text("  carregando Whisper + openWakeWord…", style="dim"), console=console,
                  refresh_per_second=12, transient=False) as live:
            r = voice_engine.diagnose(secs, tick)
    except Exception as e:
        render_error(f"{type(e).__name__}: {e}", "TESTE DE VOZ")
        return

    t = Table.grid(padding=(0, 2))
    t.add_column(style="#5fa8b8", justify="right")
    t.add_column()
    ok = lambda b: Text("✔", style="bold #39ff88") if b else Text("✘", style="bold #ff3b5f")
    t.add_row("Microfone", Text(r["mic"]))
    t.add_row("Ruído / pico", Text.assemble(ok(r["peak"] >= voice_engine.SPEECH_FLOOR),
                                            f"  {r['noise']:.0f} / {r['peak']:.0f}  (fala precisa > {voice_engine.SPEECH_FLOOR:.0f})"))
    t.add_row("Hey Jarvis", Text.assemble(ok(r["wake"] >= r["threshold"]), f"  nota {r['wake']:.2f}  (limiar {r['threshold']})"))
    t.add_row("Whisper", Text.assemble(ok(bool(r["text"])), f"  “{r['text'] or '—'}”"))
    body = [t]
    if r["problems"]:
        body += [Text(), Text("Ajustes sugeridos:", style="bold #ffd60a")]
        body += [Text(f" • {p}", style="#ffd60a") for p in r["problems"]]
    else:
        body += [Text(), Text("Tudo certo — a voz deve funcionar. Ligue com /voice on.", style="bold #39ff88")]
    console.print(Panel(Group(*body), title=Text(" 🎙 RESULTADO ", style="bold #39ff88"), title_align="left",
                        border_style="#39ff88" if not r["problems"] else "#ffd60a", box=box.ROUNDED, padding=(1, 2)))


def dispatch(text: str, speak: bool = False) -> bool:
    """Retorna False para sair."""
    low = text.strip().lower()
    if not low:
        return True
    if low in ("/exit", "/quit", "exit", "quit", "sair"):
        return False
    if low == "/kill" or core.is_kill(low):
        emergency_stop("/kill")
    elif low == "/shutdown":
        console.print(Text(" ☠ SHUTDOWN — encerrando JARVIS, Ollama e ferramentas.", style="bold #ff3b5f"))
        kill_switch.trigger("/shutdown")
    elif low == "/status":
        status_panel()
    elif low == "/agents":
        agents_panel()
    elif low in ("/cls", "/clearscreen"):
        console.clear()
    elif low.startswith("/voice"):
        if low.endswith("test"):
            voice_test()
        else:
            toggle_voice(not low.endswith("off"))
    elif low == "/help":
        console.print(Panel(Markdown(core.HELP), title=agent_title("jarvis"), title_align="left",
                            border_style=core.AGENTS["jarvis"]["color"], box=box.ROUNDED))
    else:
        run(text, speak)
    return True


# =============================================================================== PROMPT
def toolbar():
    from prompt_toolkit.formatted_text import HTML
    t = telemetry.snapshot()
    gpu = (f" │ GPU <b>{t['gpu_util']:>3}%</b> │ VRAM <b>{t['vram_used_gb']:.1f}/{t['vram_total_gb']:.0f}G</b>"
           f" │ <b>{t['gpu_temp']}°C</b>") if "gpu_util" in t else ""
    voice = " │ 🎙 <b>F2</b> falar" if state.voice else " │ 🎙 /voice on"
    return HTML(f" ⚡ CPU <b>{t['cpu']:>4.0f}%</b> │ RAM <b>{t['ram_used_gb']:.0f}/{t['ram_total_gb']:.0f}G</b>"
                f"{gpu}{voice} │ <style bg='#b0001e' fg='white'><b> F1 </b></style> PARADA ")


def interactive() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style

    kb = KeyBindings()

    @kb.add("f1")
    def _(event):                       # sem hook global (ex.: --no-hotkey), o F1 do prompt ainda funciona
        if not state.hotkey:
            threading.Thread(target=emergency_stop, args=("F1",), daemon=True).start()

    @kb.add("f2")
    def _(event):                       # push-to-talk com o terminal em foco (se o hook global faltar)
        if state.voice and getattr(state, "ptt_handle", None) is None:
            state.voice.listen_now()

    style = Style.from_dict({
        "prompt": "bold #00e5ff", "sep": "#2a5bff bold",
        "bottom-toolbar": "bg:#06141a #6fb8c8 noreverse",
        "completion-menu.completion": "bg:#0b1d24 #9fdfe8",
        "completion-menu.completion.current": "bg:#00e5ff #000000",
        "auto-suggestion": "#35505a",
    })
    completer = WordCompleter(LOCAL_COMMANDS + ENGINE_COMMANDS, ignore_case=True, sentence=True,
                              pattern=re.compile(r"^(/\S*)"))
    session = PromptSession(history=FileHistory(str(config.DATA_DIR / ".cli_history")),
                            auto_suggest=AutoSuggestFromHistory(), completer=completer,
                            complete_while_typing=True, key_bindings=kb, style=style,
                            bottom_toolbar=toolbar, refresh_interval=1.0)
    state.session = session
    message = FormattedText([("class:prompt", "JARVIS"), ("class:sep", ":\\> ")])

    while True:
        try:
            with patch_stdout(raw=True):
                text = session.prompt(message)
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        speak = False
        if text == "\x00voice":
            text, speak = state.voice_queue.get(), True
            console.print(Text.assemble(("🎙 VOCÊ › ", "bold #39ff88"), (text, "#39ff88")))
        console.print(Rule(style="#12303a"))
        if not dispatch(text, speak):
            break
    console.print(Text("\n  Até logo, senhor. Sistemas em standby.\n", style="italic #4fb3c8"))


# =============================================================================== MAIN
def start_web() -> str:
    """Sobe o terminal web (celular) em background compartilhando o mesmo Engine."""
    import uvicorn
    import jarvis_master_system as master

    master.CLI_MODE = True
    master.engine = state.engine
    srv = uvicorn.Server(uvicorn.Config(master.app, host=config.HOST, port=config.PORT,
                                        log_level="error", ws_ping_interval=20))
    threading.Thread(target=srv.run, daemon=True, name="web").start()
    for _ in range(50):
        if srv.started:
            break
        time.sleep(0.1)
    return f"http://{master.lan_ip()}:{config.PORT}/?token={config.TOKEN}"


def setup_logging(verbose: bool) -> None:
    handler = RichHandler(console=console, show_time=False, show_path=False, markup=False,
                          rich_tracebacks=True, level=logging.INFO if verbose else logging.WARNING)
    root = logging.getLogger("jarvis")
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]
    root.propagate = False


def main() -> int:
    ap = argparse.ArgumentParser(description="THE JARVIS SYSTEM — CLI")
    ap.add_argument("--once", action="append", metavar="TEXTO", help="executa o comando e sai (repetível)")
    ap.add_argument("--web", action="store_true", help="também sobe o terminal web na porta 8000")
    ap.add_argument("--no-hotkey", action="store_true", help="não registra o F1 global")
    ap.add_argument("--no-banner", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="mostra logs INFO dos subsistemas")
    ap.add_argument("--voice", action="store_true", help="liga a voz na inicialização")
    ap.add_argument("--svg", metavar="ARQ", help="salva a sessão renderizada em SVG (com --once)")
    args = ap.parse_args()

    if args.svg:
        console.record = True
    setup_logging(args.verbose)
    kill_switch.on_abort(lambda reason: state.engine.abort())

    if not args.no_hotkey and config.KILL_HOTKEY_ENABLED:
        try:
            kill_switch.start_listener(config.KILL_HOTKEY, action=emergency_stop)
            state.hotkey = True
        except Exception as e:
            log.warning("Hotkey global indisponível (%s) — F1 só funciona com o terminal em foco.", e)

    web_url = start_web() if args.web else None
    if not args.no_banner:
        banner(web_url)

    try:
        if args.voice or config.VOICE_ENABLED:
            toggle_voice(True)
        if args.once:
            for text in args.once:
                console.print(Text.assemble(("JARVIS", "bold #00e5ff"), (":\\> ", "bold #2a5bff"), (text, "")))
                dispatch(text)
        else:
            interactive()
    finally:
        kill_switch.stop_listener()
        if state.voice:
            state.voice.stop()
        if args.svg:
            console.save_svg(args.svg, title="THE JARVIS SYSTEM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
