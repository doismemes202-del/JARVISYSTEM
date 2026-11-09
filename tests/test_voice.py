"""Teste de ponta a ponta da voz SEM precisar falar: um "microfone falso" toca
'Hey Jarvis' + um comando sintetizados pelo Piper dentro do motor de voz.

    python tests/test_voice.py
"""
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from modules import voice_engine as ve  # noqa: E402

results = {}


def synth16k(tts: ve.PiperTTS, text: str) -> np.ndarray:
    fd, p = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    tts._synth(text, p)
    with wave.open(p, "rb") as wf:
        sr = wf.getframerate()
        a = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    os.remove(p)
    n = int(len(a) * 16000 / sr)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.int16)


# ------------------------------------------------------------------ 1) loop completo com microfone falso
tts = ve.PiperTTS()
silence = lambda s: np.random.normal(0, 25, int(16000 * s)).astype(np.int16)   # ruído ambiente leve
command = synth16k(tts, "Plato, quanto é quinze por cento de dois mil e quatrocentos?")
# a voz pt-BR do Piper lendo "Rêi Djárvis" ≈ pronúncia americana de "Hey Jarvis" (a que o modelo reconhece)
audio = np.concatenate([silence(1.0), synth16k(tts, "Rêi Djárvis!"), silence(0.6), command, silence(3.0)])


class FakeStream:
    def __init__(self, *a, **k):
        self.pos = 0

    def start(self):
        pass

    def read(self, n):
        time.sleep(n / 16000)              # tempo real, como um microfone de verdade
        chunk = audio[self.pos:self.pos + n]
        self.pos += n
        if len(chunk) < n:
            time.sleep(0.05)
            chunk = np.concatenate([chunk, silence((n - len(chunk)) / 16000)])
        return chunk.reshape(-1, 1), False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


import sounddevice as sd  # noqa: E402
real_stream = sd.InputStream
sd.InputStream = FakeStream
events, commands = [], []
eng = ve.VoiceEngine(commands.append, on_event=lambda k, i: events.append((k, i)))
eng._beep = lambda *a, **k: None
eng.start()
deadline = time.time() + 120
while not commands and time.time() < deadline and eng.is_alive():
    time.sleep(0.1)
eng.stop()
kinds = [k for k, _ in events]
print("eventos:", events)
print("comando recebido:", commands)
results["wake word detectada"] = "wake" in kinds
results["evento 'ouvindo'"] = "listening" in kinds
results["comando transcrito"] = bool(commands) and "plato" in commands[0].lower()

# ------------------------------------------------------------------ 1b) push-to-talk (F2): sem wake word
audio = np.concatenate([silence(2.0), command, silence(3.0)])
events2, commands2 = [], []
eng2 = ve.VoiceEngine(commands2.append, on_event=lambda k, i: events2.append((k, i)))
eng2._beep = lambda *a, **k: None
eng2.start()
eng2.ready.wait(120)
time.sleep(1.0)
eng2.listen_now()                       # = apertar F2
deadline = time.time() + 60
while not commands2 and time.time() < deadline and eng2.is_alive():
    time.sleep(0.1)
eng2.stop()
sd.InputStream = real_stream
print("F2 eventos:", events2, "| comando:", commands2)
results["F2 (push-to-talk) transcreve"] = ("wake", "F2") in events2 and bool(commands2)

# ------------------------------------------------------------------ 2) voz → prompt da CLI (prompt_toolkit)
from prompt_toolkit import PromptSession  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402
import functools  # noqa: E402

with create_pipe_input() as inp:
    session = PromptSession(input=inp, output=DummyOutput())

    def fire():
        time.sleep(0.8)
        app = session.app
        app.loop.call_soon_threadsafe(functools.partial(app.exit, result="\x00voice"))

    threading.Thread(target=fire, daemon=True).start()
    got = session.prompt("JARVIS:\\> ")
results["voz interrompe o prompt"] = got == "\x00voice"

# ------------------------------------------------------------------ 3) /voice test com microfone real
r = ve.diagnose(1.5)
print("diagnóstico (silêncio, mic real):", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
results["/voice test executa"] = "mic" in r and "problems" in r

print("\nRESUMO")
for k, v in results.items():
    print(f"  {'✔' if v else '✘'} {k}")
sys.exit(0 if all(results.values()) else 1)
