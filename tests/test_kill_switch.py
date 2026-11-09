"""Teste do binding global do kill switch (modo DRY-RUN — nada é encerrado).

    python tests/test_kill_switch.py           # injeta um F1 sintético e verifica o disparo
    python tests/test_kill_switch.py --manual  # aguarda você apertar F1 de verdade (15 s)
"""
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["JARVIS_KILL_DRY_RUN"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(name)s │ %(message)s")

from modules import kill_switch  # noqa: E402

# processo-filho "isca" para provar que a árvore de filhos é encontrada
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
kill_switch.register_process(child)

fired = []
kill_switch.on_kill(fired.append)
kill_switch.start_listener("f1")

if "--manual" in sys.argv:
    print("Aperte F1 em qualquer janela nos próximos 15 s...")
    deadline = time.time() + 15
else:
    time.sleep(1.0)  # o hook do Windows sobe numa thread própria; aguarda ficar ativo
    # keyboard.send() é ignorado pelo próprio hook da lib; keybd_event simula a tecla física.
    import ctypes
    VK_F1, SCAN_F1, KEYUP = 0x70, 0x3B, 0x0002
    ctypes.windll.user32.keybd_event(VK_F1, SCAN_F1, 0, 0)
    ctypes.windll.user32.keybd_event(VK_F1, SCAN_F1, KEYUP, 0)
    deadline = time.time() + 3

while not fired and time.time() < deadline:
    time.sleep(0.05)
time.sleep(0.5)  # deixa o trigger terminar a coleta de alvos

child.kill()
kill_switch.stop_listener()

report = kill_switch.last_report
ok = bool(fired) and report.get("dry_run") and child.pid in report.get("targets", {})
print("\nRESULTADO:", "PASSOU ✔" if ok else "FALHOU ✘")
print("  disparou:", fired)
print("  alvos identificados:", {pid: why for pid, why in report.get("targets", {}).items()})
sys.exit(0 if ok else 1)
