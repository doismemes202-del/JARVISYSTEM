"""PLATO — cálculos executados de verdade em Python (em vez de o modelo "chutar" a conta).

Fluxo: o modelo escreve um programa curto → validamos o código (AST) → executamos num processo
Python isolado (-I, timeout) → os números da saída são a verdade usada na explicação final.

Segurança: só são aceitos imports de bibliotecas matemáticas; funções de arquivo, rede, sistema,
eval/exec e atributos "_privados" são bloqueados antes da execução. O processo é registrado no
kill switch (F1 o encerra).
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys

from . import kill_switch

ALLOWED_MODULES = {"math", "cmath", "fractions", "decimal", "statistics", "itertools", "functools",
                   "sympy", "datetime", "numbers"}
BLOCKED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "vars",
                 "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview",
                 "__builtins__", "os", "sys", "subprocess", "shutil", "socket", "pathlib", "importlib",
                 # funções do sympy que avaliam texto como código (eval/exec por dentro)
                 "sympify", "parse_expr", "lambdify", "S", "external", "utilities", "importtools"}
TIMEOUT_S = 20
MAX_OUT = 4000

_CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S | re.I)


class UnsafeCode(ValueError):
    pass


def extract_code(text: str) -> str:
    m = _CODE_RE.search(text or "")
    code = m.group(1) if m else (text or "").replace("```python", "").replace("```", "")
    # a persona do JARVIS às vezes assina "💡 Telemetria..." até dentro do código — remove
    lines = [ln for ln in code.splitlines() if "💡" not in ln and not re.match(r"\s*\**\s*Telemetria", ln)]
    return "\n".join(lines).strip()


def check(code: str) -> None:
    """Levanta UnsafeCode se o programa tentar algo além de matemática."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise UnsafeCode(f"erro de sintaxe na linha {e.lineno}: {e.msg}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [(node.module or "").split(".")[0]]
        else:
            mods = []
        bad = [m for m in mods if m not in ALLOWED_MODULES]
        if bad:
            raise UnsafeCode(f"import não permitido: {', '.join(bad)}")
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            raise UnsafeCode(f"uso não permitido: {node.id}")
        if isinstance(node, ast.alias) and (node.asname or node.name).split(".")[-1] in BLOCKED_NAMES:
            raise UnsafeCode(f"import não permitido: {node.name}")
        if isinstance(node, ast.Attribute) and node.attr in BLOCKED_NAMES:
            raise UnsafeCode(f"uso não permitido: {node.attr}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise UnsafeCode(f"atributo não permitido: {node.attr}")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise UnsafeCode("global/nonlocal não permitido")


# Proteção contra o erro clássico de ponto flutuante: 1.2 // 0.4 dá 2.0 (e não 3) em Python.
# Toda divisão inteira (//) do programa passa a ser feita com frações exatas, e math.floor/ceil
# ignoram resíduos como 2.9999999999.
PRELUDE = '''\
import math as _jm
from fractions import Fraction as _JF
def _jarvis_exact(v):
    return _JF(repr(round(v, 12))) if isinstance(v, float) else v
def _jarvis_fdiv(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return float(_jarvis_exact(a) // _jarvis_exact(b))
    return a // b
_jf, _jc = _jm.floor, _jm.ceil
_jm.floor = lambda x: _jf(round(x, 9)) if isinstance(x, float) else _jf(x)
_jm.ceil = lambda x: _jc(round(x, 9)) if isinstance(x, float) else _jc(x)
'''


class _ExactFloorDiv(ast.NodeTransformer):
    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.FloorDiv):
            return ast.Call(ast.Name("_jarvis_fdiv", ast.Load()), [node.left, node.right], [])
        return node

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.FloorDiv) and isinstance(node.target, ast.Name):
            load = ast.Name(node.target.id, ast.Load())
            return ast.Assign([node.target], ast.Call(ast.Name("_jarvis_fdiv", ast.Load()), [load, node.value], []))
        return node


def prepare(code: str) -> str:
    """Valida (levanta UnsafeCode) e devolve o programa pronto para executar."""
    check(code)
    tree = ast.fix_missing_locations(_ExactFloorDiv().visit(ast.parse(code)))
    return PRELUDE + ast.unparse(tree)


def run(code: str, timeout: float = TIMEOUT_S) -> tuple[bool, str]:
    """Executa o programa validado. Retorna (ok, saída ou erro)."""
    try:
        program = prepare(code)
    except UnsafeCode as e:
        return False, f"Código recusado: {e}"
    proc = subprocess.Popen([sys.executable, "-I", "-X", "utf8", "-c", program],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    kill_switch.register_process(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return False, f"O cálculo passou de {timeout:.0f} s e foi interrompido."
    if proc.returncode != 0:
        lines = [ln for ln in (err or "").strip().splitlines() if ln.strip()]
        return False, "\n".join(lines[-4:]) or f"código de saída {proc.returncode}"
    out = (out or "").strip()
    if not out:
        return False, "O programa não imprimiu nenhum resultado (faltou print)."
    return True, out[:MAX_OUT]


CODE_DIRECTIVE = (
    "[SUBSISTEMA PLATO — CÁLCULO EXATO] Escreva um programa Python curto que RESOLVA o problema abaixo.\n"
    "Regras:\n"
    "- Interprete o problema com cuidado e use variáveis com nomes claros e comentários em português.\n"
    "- Use print() para mostrar cada etapa importante e o resultado final, com rótulo e unidade "
    "(ex.: print('Resultado final:', round(valor, 2), 'unidade')). Número puro (sem unidade) fica sem unidade.\n"
    "- Para equações, sistemas, derivadas e integrais use sympy; para o resto, math/fractions/decimal/statistics.\n"
    "- Converta tudo para a MESMA unidade antes de calcular (de preferência inteiros: mm, cm, centavos).\n"
    "- Arredonde só na hora de imprimir. Quantidades de peças/itens inteiros: arredonde para cima (math.ceil) "
    "quando faltar material, e para baixo (//) quando a pergunta for quantas cabem.\n"
    "- Não leia arquivos, não use input() e não acesse rede ou sistema.\n"
    "Responda SOMENTE com um único bloco ```python.\n\nProblema: {ask}")

FIX_DIRECTIVE = (
    "O programa abaixo falhou. Corrija-o e responda SOMENTE com o programa completo num bloco ```python.\n\n"
    "Problema: {ask}\n\n```python\n{code}\n```\n\nErro:\n{error}")

EXPLAIN_DIRECTIVE = (
    "[SUBSISTEMA PLATO ATIVO] Responda como PLATO ao pedido do usuário usando os resultados abaixo, que foram "
    "CALCULADOS E VERIFICADOS executando Python (são exatos — não refaça as contas de cabeça e não altere "
    "nenhum número).\n\nPedido: {ask}\n\nPrograma executado:\n```python\n{code}\n```\n\nSaída do programa:\n"
    "```text\n{out}\n```\n\nExplique a solução passo a passo em português, em texto simples (sem LaTeX, use "
    "× ÷ e ²), com as unidades, e destaque o resultado final em negrito na última linha. Valores em reais no "
    "formato brasileiro (R$ 1.234,56). Não inclua o código na resposta, não mencione Python, programa ou "
    "subsistemas, e não invente unidades: números sem unidade (como raízes de equação) ficam sem unidade.")
