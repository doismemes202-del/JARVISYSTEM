r"""Teste da MONDAY sem enviar nada de verdade: contatos temporários e envio simulado.

    .\.venv\Scripts\python tests\test_monday.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from modules import config, core, tool_whatsapp  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "contacts.json"
tmp.write_text(json.dumps({"Mãe": "+5511900000001",
                           "fornecedor": {"telefone": "+5511900000002", "auto": True}}), encoding="utf-8")
config.CONTACTS_FILE = tmp
sent: list[tuple[str, str]] = []
tool_whatsapp.send_message = lambda phone, text: sent.append((phone, text))   # nada sai de verdade

results = {}
pa = core.pending_answer
results["'confirmar' / 'pode mandar' / 'Jarvis, envia' confirmam"] = all(
    pa(t) == "send" for t in ("confirmar", "Pode mandar!", "Jarvis, envia", "sim, manda"))
results["'cancelar' / 'não' / 'esquece' cancelam"] = all(pa(t) == "cancel" for t in ("cancelar", "Não.", "esquece"))
results["frases longas ou 'ok' não confirmam"] = all(
    pa(t) is None for t in ("ok", "quanto é 2 + 2 vezes 3?", "manda um texto sobre gatos para mim agora"))
results["detecta pedido de mensagem"] = all(core.is_message_request(t) for t in (
    "Monday, manda pra mãe que vou atrasar", "avisa o fornecedor que o pedido chegou", "envia uma mensagem pro João"))
results["não confunde conversa com mensagem"] = not any(core.is_message_request(t) for t in (
    "Quanto é 15% de 2400?", "Bom dia, Jarvis"))

eng = core.Engine()


def step(text: str):
    t = time.time()
    r = eng.handle(text)
    pend = tool_whatsapp.get_pending()
    print(f"[{time.time() - t:4.1f}s] {text!r:55} → enviados={len(sent)} pendente={pend.label if pend else '-'}"
          f" | {r.qa or '-'} | {r.body.splitlines()[0][:70]}")
    if r.qa == "FLAGGED":
        print("      ressalvas da Friday:", r.notes[:300].replace("\n", " "))
    return r


# 1) /zap! para contato liberado → envia sozinho
r = step("/zap! fornecedor | avisar que o pedido 123 chegou e está conferido")
results["/zap! envia direto para contato com auto"] = len(sent) == 1 and sent[0][0] == "+5511900000002" \
    and tool_whatsapp.get_pending() is None

# 2) /zap! para contato NÃO liberado → fica pendente; "confirmar" envia
r = step("/zap! mae | dizer que vou chegar às 20h")
results["/zap! para contato sem auto só cria rascunho"] = len(sent) == 1 and tool_whatsapp.get_pending() is not None
results["rascunho tem fala para voz e pede confirmação"] = r.extra.get("await_confirm") and "confirmar" in r.extra["speech"]
step("confirmar")
results["'confirmar' envia o rascunho"] = len(sent) == 2 and sent[1][0] == "+5511900000001"

# 3) pedido em linguagem natural (voz) → rascunho, nunca envio direto
r = step("Monday, manda pra mãe que eu vou atrasar uns 30 minutos")
results["voz: 'manda pra mãe…' gera rascunho sem enviar"] = len(sent) == 2 and \
    (tool_whatsapp.get_pending() or tool_whatsapp.PendingMessage("", "", "")).phone == "+5511900000001"
step("cancelar")
results["'cancelar' descarta sem enviar"] = len(sent) == 2 and tool_whatsapp.get_pending() is None

# 4) contato desconhecido
r = step("avisa o Carlos que a reunião mudou para as 15h")
results["contato desconhecido não envia e explica"] = len(sent) == 2 and "Não achei" in r.body

# 5) pedido técnico com 'envia' não vira mensagem
r = step("Romeu, escreva uma função Python que envia um e-mail com smtplib")
results["pedido de código com 'envia' vai para o Romeu"] = r.agent == "romeu" and len(sent) == 2

print("\nRESUMO")
for k, v in results.items():
    print(f"  {'✔' if v else '✘'} {k}")
print(f"  {sum(map(bool, results.values()))}/{len(results)}")
sys.exit(0 if all(results.values()) else 1)
