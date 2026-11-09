"""PAPA — Engenharia CAD/CAM: scripts parametrizados do Fusion 360 + Open Interpreter (--os).

A API do Fusion 360 (módulo `adsk`) só existe DENTRO do Fusion. Por isso o PAPA gera
scripts em  %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\Scripts\\<nome>\\  e você os executa em
Fusion → UTILITIES → ADD-INS → Scripts and Add-Ins (Shift+S) → <nome> → Run.

Para ações físicas na interface (cliques, menus), o Open Interpreter em modo OS é aberto
num console próprio, com o modelo de visão local do Ollama. O kill switch F1 o encerra.

Uso direto do runner do Open Interpreter:
    python -m modules.tool_fusion --oi "abra o Fusion e crie um novo design"
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config

log = logging.getLogger("jarvis.papa")

# ------------------------------------------------------------------ templates
PLATE_DEFAULTS = {          # nome: (valor, unidade)
    "comprimento": (100.0, "mm"),
    "largura": (60.0, "mm"),
    "espessura": (5.0, "mm"),
    "diametro_furo": (5.0, "mm"),
    "margem_furo": (8.0, "mm"),
}
PLATE_ALIASES = {"c": "comprimento", "l": "largura", "e": "espessura", "furo": "diametro_furo",
                 "d": "diametro_furo", "margem": "margem_furo", "m": "margem_furo"}

PLATE_TEMPLATE = r'''"""Placa parametrizada com 4 furos — gerada pelo J.A.R.V.I.S / PAPA em __STAMP__."""
import adsk.core, adsk.fusion, traceback

PARAMS = __PARAMS__   # nome: (valor, unidade) -> viram User Parameters editáveis


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("Abra ou crie um Design antes de rodar o script.")
            return
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType

        # 1) Parâmetros de usuário (Modify > Change Parameters)
        up = design.userParameters
        for name, (value, unit) in PARAMS.items():
            expr = f"{value} {unit}"
            p = up.itemByName(name)
            if p:
                p.expression = expr
            else:
                up.add(name, adsk.core.ValueInput.createByString(expr), unit, "JARVIS/PAPA")

        cm = lambda key: PARAMS[key][0] / 10.0  # a API trabalha em centímetros
        L, W = cm("comprimento"), cm("largura")
        r, mg = cm("diametro_furo") / 2.0, cm("margem_furo")
        if 2 * (mg + r) >= min(L, W):
            ui.messageBox("Margem/furo incompatíveis com o tamanho da placa.")
            return

        root = design.rootComponent
        sk = root.sketches.add(root.xYConstructionPlane)
        sk.name = "PAPA_placa"
        P = adsk.core.Point3D.create

        # 2) Retângulo cotado pelos parâmetros
        rect = sk.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 0, 0), P(L, W, 0))
        dims = sk.sketchDimensions
        H = adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation
        V = adsk.fusion.DimensionOrientations.VerticalDimensionOrientation
        dims.addDistanceDimension(rect.item(0).startSketchPoint, rect.item(0).endSketchPoint,
                                  H, P(L / 2, -1, 0)).parameter.expression = "comprimento"
        dims.addDistanceDimension(rect.item(1).startSketchPoint, rect.item(1).endSketchPoint,
                                  V, P(L + 1, W / 2, 0)).parameter.expression = "largura"
        sk.geometricConstraints.addCoincident(rect.item(0).startSketchPoint, sk.originPoint)

        # 3) Furos nos cantos
        circles = sk.sketchCurves.sketchCircles
        for x, y in ((mg, mg), (L - mg, mg), (mg, W - mg), (L - mg, W - mg)):
            c = circles.addByCenterRadius(P(x, y, 0), r)
            dims.addDiameterDimension(c, P(x + r, y + r, 0)).parameter.expression = "diametro_furo"

        # 4) Extrusão do perfil externo (o que contém os furos como loops internos)
        prof = max(sk.profiles, key=lambda p: p.profileLoops.count)
        ext = root.features.extrudeFeatures
        inp = ext.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        inp.setDistanceExtent(False, adsk.core.ValueInput.createByString("espessura"))
        body = ext.add(inp).bodies.item(0)
        body.name = "PAPA_placa"

        ui.messageBox("PAPA: placa gerada. Edite em Modify > Change Parameters.")
    except Exception:
        if ui:
            ui.messageBox("Falha:\n{}".format(traceback.format_exc()))
'''

MANIFEST = {
    "autodeskProduct": "Fusion360",
    "type": "script",
    "author": "J.A.R.V.I.S / PAPA",
    "description": {"": ""},
    "supportedOS": "windows|mac",
    "editEnabled": True,
}


def slug(text: str, maxlen: int = 32) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:maxlen]
    return s or "script"


def save_script(name: str, code: str, description: str = "") -> Path:
    """Grava <Scripts>/<name>/<name>.py + .manifest (formato que o Fusion reconhece)."""
    folder = config.FUSION_SCRIPTS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.py").write_text(code, encoding="utf-8")
    manifest = dict(MANIFEST, description={"": description[:200] or "Gerado pelo JARVIS"})
    (folder / f"{name}.manifest").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Script Fusion salvo: %s", folder)
    return folder / f"{name}.py"


def parse_params(text: str, defaults: dict, aliases: dict | None = None) -> dict:
    """'comprimento=120 l=80 furo=6.5' → {nome: (valor, unidade)}."""
    params = dict(defaults)
    for key, val, unit in re.findall(r"([a-zA-Z_]+)\s*=\s*([\d.,]+)\s*(mm|cm|in)?", text):
        key = (aliases or {}).get(key.lower(), key.lower())
        if key in params:
            params[key] = (float(val.replace(",", ".")), unit or params[key][1])
    return params


def generate_plate(text: str) -> tuple[Path, dict]:
    params = parse_params(text, PLATE_DEFAULTS, PLATE_ALIASES)
    code = (PLATE_TEMPLATE
            .replace("__PARAMS__", repr(params))
            .replace("__STAMP__", f"{datetime.now():%Y-%m-%d %H:%M}"))
    name = f"jarvis_placa_{datetime.now():%Y%m%d_%H%M%S}"
    return save_script(name, code, "Placa parametrizada com 4 furos"), params


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return max(blocks, key=len).strip() if blocks else text.strip()


def validate_fusion_script(code: str) -> list[str]:
    """Checagens estáticas rápidas antes de salvar."""
    problems = []
    try:
        compile(code, "<fusion_script>", "exec")
    except SyntaxError as e:
        problems.append(f"Erro de sintaxe na linha {e.lineno}: {e.msg}")
    if "def run(" not in code:
        problems.append("Falta a função run(context).")
    if "adsk" not in code:
        problems.append("Não importa adsk.core/adsk.fusion.")
    return problems


# ------------------------------------------------------------------ Open Interpreter
def launch_open_interpreter(instruction: str) -> subprocess.Popen:
    """Abre o Open Interpreter em modo OS num console próprio (visível e interrompível)."""
    from . import kill_switch
    python = config.OI_PYTHON or sys.executable
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    proc = subprocess.Popen([python, "-m", "modules.tool_fusion", "--oi", instruction],
                            cwd=str(config.BASE_DIR), creationflags=flags)
    kill_switch.register_process(proc)
    log.warning("Open Interpreter (--os, %s) iniciado PID %s.", config.VISION_MODEL, proc.pid)
    return proc


def _run_open_interpreter(instruction: str) -> None:
    try:
        from interpreter import interpreter
    except ImportError:
        print("open-interpreter não instalado neste Python. Veja README (OI_PYTHON).")
        input("Enter para fechar...")
        return
    interpreter.os = True
    interpreter.llm.model = f"ollama/{config.VISION_MODEL}"
    interpreter.llm.api_base = config.OLLAMA_URL
    interpreter.llm.supports_vision = True
    interpreter.llm.supports_functions = False
    interpreter.llm.context_window = 8192
    interpreter.llm.max_tokens = 1024
    interpreter.auto_run = False  # cada ação pede confirmação neste console
    interpreter.system_message += (
        "\nVocê é o braço operacional do PAPA (engenheiro CAD/CAM do JARVIS). "
        "Foco: Autodesk Fusion 360 no Windows 11. Responda em português.")
    print("=" * 60, "\n PAPA // OPEN INTERPRETER (--os)  |  F1 = KILL SWITCH\n", "=" * 60)
    interpreter.chat(instruction)
    interpreter.chat()  # continua interativo


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--oi":
        _run_open_interpreter(" ".join(sys.argv[2:]))
    else:
        print(__doc__)
