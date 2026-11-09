r"""Teste de precisão do JARVIS com o Ollama de verdade: entendimento do pedido, contas e textos longos.

    .\.venv\Scripts\python tests\test_accuracy.py
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from modules import core, tool_math  # noqa: E402

# ------------------------------------------------------------------ sandbox (sem modelo)
SAFE = [("print(2 + 2)", True), ("import os\nprint(os.getcwd())", False), ("print(open('x').read())", False),
        ("print(().__class__)", False), ("from sympy import sympify\nprint(sympify('1+1'))", False),
        ("import sympy as sp\nx = sp.symbols('x')\nprint(sp.solve(x**2 - 4, x))", True)]
results = {}
for code, should_run in SAFE:
    ok, out = tool_math.run(code)
    results[f"sandbox: {code.splitlines()[-1][:40]!r} {'executa' if should_run else 'bloqueado'}"] = ok == should_run

MATH = [
    ("Quanto é 15% de 2400?", [360]),
    ("Um motor gira a 1750 rpm com polia de 100 mm acionando uma polia de 250 mm. Qual a rotação de saída?", [700]),
    ("Um produto custa R$ 89,90. Tem 12% de desconto e depois 8% de imposto sobre o valor com desconto. "
     "Qual o preço final?", [85.44]),
    ("Resolva 3x² - 12x + 9 = 0", [1, 3]),
    ("Investi R$ 5000 a juros compostos de 1,2% ao mês por 18 meses. Quanto terei?", [round(5000 * 1.012 ** 18, 2)]),
    ("Quantas peças de 60x40 cm cabem em uma chapa de 2,4 x 1,2 m?", [12]),
]
TIPOS = [("Bom dia, Jarvis! Tudo certo?", "conversa"),
         ("Escreva um texto de 300 palavras sobre a importância da manutenção preventiva em máquinas CNC.", "texto"),
         ("Romeu, como eu leio um arquivo JSON em Python?", "codigo"),
         ("Me lembra de ligar para o fornecedor amanhã às 9h", "agenda"),
         ("Quanto dá 3 parafusos de R$ 0,45 mais 2 porcas de R$ 0,30?", "calculo"),
         ("Como faço um chanfro de 2 mm numa aresta no Fusion?", "cad")]


ok, out = tool_math.run("print(1.2 // 0.4, 2.4 // 0.6)")
results["sandbox: 1.2 // 0.4 dá 3 (sem erro de ponto flutuante)"] = ok and out.split() == ["3.0", "4.0"]


def nums(s: str) -> list[float]:
    """Números em formato brasileiro (6.197,54) ou com ponto decimal (700.0)."""
    vals = []
    for tok in re.findall(r"-?\d[\d.,]*", s):
        tok = tok.rstrip(".,")
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", tok):
            tok = tok.replace(".", "").replace(",", ".")
        else:
            tok = tok.replace(",", ".")
        try:
            vals.append(float(tok))
        except ValueError:
            pass
    return vals


eng = core.Engine()
only = sys.argv[1:]
if not only or "tipo" in only:
    for q, exp in TIPOS:
        t = time.time()
        got = eng.understand(q)
        print(f"[{time.time() - t:4.1f}s] tipo {got:>8} (esperado {exp}) | {q[:60]}")
        results[f"entende '{q[:40]}…' como {exp}"] = got == exp

if not only or "math" in only:
    for q, exp in MATH:
        eng.memory.clear()
        t = time.time()
        r = eng.handle(q)
        lines = [ln for ln in r.body.split("🧮")[0].strip().splitlines() if ln.strip()]
        last = lines[-1]
        found = nums(" ".join(lines[-3:]))            # o resultado final da explicação, não só a saída
        ok = all(any(abs(f - e) < 0.011 for f in found) for e in exp)
        print(f"[{time.time() - t:5.1f}s] {r.qa:9} {'✔' if ok else '✘'} esperado {exp} | {last[:110]}")
        results[f"conta: {q[:45]}…"] = ok

if not only or "texto" in only:
    eng.memory.clear()
    t = time.time()
    r = eng.handle(TIPOS[1][0])
    words = len(re.findall(r"\w+", r.body))
    print(f"[{time.time() - t:5.1f}s] texto longo: {words} palavras, selo {r.qa}")
    results["texto pedido com 300 palavras tem 220+ palavras"] = words >= 220

if not only or "codigo" in only:
    eng.memory.clear()
    t = time.time()
    r = eng.handle("Romeu, escreva uma função Python que receba uma lista de números e retorne a média e a mediana.")
    dt = time.time() - t
    print(f"[{dt:5.1f}s] código: selo {r.qa} · {r.meta}")
    results["Romeu entrega código auditado em < 120 s"] = "```" in r.body and r.qa in ("PASSED", "CORRECTED") and dt < 120

print("\nRESUMO")
for k, v in results.items():
    print(f"  {'✔' if v else '✘'} {k}")
print(f"  {sum(results.values())}/{len(results)}")
sys.exit(0 if all(results.values()) else 1)
