"""Pipeline de voz: openWakeWord ("Hey/Ok Jarvis") → gravação com VAD → Faster-Whisper → callback.
Saída falada via Piper TTS.

Wake word: o openWakeWord traz o modelo pré-treinado "hey_jarvis". Para "Ok Jarvis" treine um
modelo customizado (notebook oficial do openWakeWord) e aponte WAKEWORD_MODEL para o .onnx.

Teste isolado:
    python -m modules.voice_engine          # escuta e imprime as transcrições
    python -m modules.voice_engine --say "Sistemas online, senhor."
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger("jarvis.voice")

SAMPLE_RATE = 16000
FRAME = 1280                 # 80 ms — tamanho de frame exigido pelo openWakeWord
SILENCE_END_S = 1.2
MAX_UTTERANCE_S = 15.0
START_TIMEOUT_S = 5.0


def _add_cuda_dll_dirs() -> None:
    """Expõe as DLLs de cuBLAS/cuDNN instaladas via pip ao CTranslate2 no Windows."""
    if os.name != "nt":
        return
    for mod in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        try:
            pkg = __import__(mod, fromlist=["__path__"])
            for base in pkg.__path__:
                bin_dir = Path(base) / "bin"
                if bin_dir.is_dir():
                    os.add_dll_directory(str(bin_dir))
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            continue


# ====================================================================== TTS
class PiperTTS:
    def __init__(self) -> None:
        self._voice = None
        self._lock = threading.Lock()

    def _synth(self, text: str, path: str) -> None:
        if config.PIPER_EXE:
            subprocess.run([config.PIPER_EXE, "--model", str(config.PIPER_VOICE), "--output_file", path],
                           input=text.encode("utf-8"), check=True, capture_output=True)
            return
        from piper import PiperVoice
        if self._voice is None:
            if not config.PIPER_VOICE.exists():
                raise FileNotFoundError(f"Voz Piper não encontrada: {config.PIPER_VOICE} (rode setup.ps1)")
            self._voice = PiperVoice.load(str(config.PIPER_VOICE))
        with wave.open(path, "wb") as wf:
            if hasattr(self._voice, "synthesize_wav"):   # piper-tts >= 1.3
                self._voice.synthesize_wav(text, wf)
            else:                                         # piper-tts 1.2
                self._voice.synthesize(text, wf)

    def speak(self, text: str) -> None:
        import numpy as np
        import sounddevice as sd
        text = text.strip()
        if not text:
            return
        with self._lock:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                self._synth(text, path)
                with wave.open(path, "rb") as wf:
                    sr, ch = wf.getframerate(), wf.getnchannels()
                    data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                if ch > 1:
                    data = data.reshape(-1, ch)
                sd.play(data, sr)
                sd.wait()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass


def speakable(text: str, limit: int = 600) -> str:
    """Remove código, selos e markdown — o que sobra é falado."""
    text = re.sub(r"```.*?```", " Código enviado ao terminal. ", text, flags=re.S)
    for marker in ("💡 Telemetria", "🛡️", "⚠️ Friday"):
        idx = text.find(marker)
        if idx >= 0:
            text = text[:idx]
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"https?://\S+", "link no terminal", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ====================================================================== Engine
SPEECH_FLOOR = 180.0          # RMS mínimo de fala (int16) — headsets com ganho baixo ficam ~200-600


class VoiceEngine(threading.Thread):
    """on_event(tipo, info) recebe: ready, wake, listening, heard, nothing, error — para o front-end
    mostrar ao usuário o que o microfone está fazendo."""

    def __init__(self, on_command: Callable[[str], None],
                 on_event: Callable[[str, str], None] | None = None) -> None:
        super().__init__(daemon=True, name="voice-engine")
        self.on_command = on_command
        self.on_event = on_event or (lambda kind, info: None)
        self.tts = PiperTTS()
        self._stop_evt = threading.Event()
        self._ptt = threading.Event()           # push-to-talk (F2): ouvir sem precisar da wake word
        self._speaking = threading.Event()
        self._noise = 300.0
        self.ready = threading.Event()
        self.error: str | None = None

    # ------------------------------------------------------------ setup
    def _load_wakeword(self):
        import openwakeword
        from openwakeword.model import Model
        target = config.WAKEWORD_MODEL
        if not target.endswith((".onnx", ".tflite")):
            try:
                openwakeword.utils.download_models(model_names=[target])
            except Exception as e:
                log.debug("download_models: %s", e)
        return Model(wakeword_models=[target], inference_framework="onnx")

    def _load_whisper(self):
        from faster_whisper import WhisperModel
        import numpy as np
        if config.WHISPER_DEVICE == "cuda":
            _add_cuda_dll_dirs()
            try:
                m = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type="int8_float16")
                list(m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language=config.WHISPER_LANGUAGE)[0])
                log.info("Faster-Whisper '%s' em CUDA.", config.WHISPER_MODEL)
                return m
            except Exception as e:
                log.warning("Whisper CUDA indisponível (%s) — usando CPU.", e)
        log.info("Faster-Whisper '%s' em CPU (int8).", config.WHISPER_MODEL)
        return WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")

    # ------------------------------------------------------------ audio helpers
    @staticmethod
    def _rms(frame) -> float:
        import numpy as np
        return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))

    def _beep(self, freq: float = 880.0, dur: float = 0.12) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            t = np.linspace(0, dur, int(22050 * dur), False)
            sd.play((0.25 * np.sin(2 * np.pi * freq * t)).astype(np.float32), 22050)
        except Exception:
            pass

    def _record_utterance(self, stream):
        import numpy as np
        threshold = min(max(self._noise * 3.0, SPEECH_FLOOR), 900.0)   # teto: nunca exige fala gritada
        chunks, started, silent, t0 = [], False, 0.0, time.time()
        frame_s = FRAME / SAMPLE_RATE
        while not self._stop_evt.is_set():
            frame = stream.read(FRAME)[0][:, 0]
            loud = self._rms(frame) > threshold
            if not started:
                if loud:
                    started = True
                    chunks.append(frame)
                elif time.time() - t0 > START_TIMEOUT_S:
                    return None
                continue
            chunks.append(frame)
            silent = 0.0 if loud else silent + frame_s
            if silent >= SILENCE_END_S or len(chunks) * frame_s >= MAX_UTTERANCE_S:
                break
        return np.concatenate(chunks) if chunks else None

    def _transcribe(self, whisper, audio) -> str:
        import numpy as np
        segments, _ = whisper.transcribe(audio.astype(np.float32) / 32768.0,
                                         language=config.WHISPER_LANGUAGE, beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        try:
            import sounddevice as sd
            oww = self._load_wakeword()
            whisper = self._load_whisper()
        except Exception as e:
            self.error = str(e)
            log.error("Voz desativada — dependências ausentes/falha: %s", e)
            self.ready.set()
            return

        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME)
            stream.start()
        except Exception as e:
            self.error = f"Microfone indisponível: {e}"
            log.error(self.error)
            self.ready.set()
            return
        self.mic_name = sd.query_devices(kind="input")["name"]
        log.info("🎙  Escuta ativa (%s). Diga 'Hey Jarvis'.", self.mic_name)
        self.ready.set()
        self.on_event("ready", self.mic_name)
        with stream:
            while not self._stop_evt.is_set():
                frame = stream.read(FRAME)[0][:, 0]
                if self._speaking.is_set():
                    continue                       # evita o Jarvis ouvir a própria voz
                level = self._rms(frame)
                if level < max(self._noise * 2.0, 300.0):   # só aprende ruído de fundo, nunca a fala
                    self._noise = 0.95 * self._noise + 0.05 * level
                scores = oww.predict(frame)
                score = max(scores.values()) if scores else 0.0
                if self._ptt.is_set():
                    self._ptt.clear()
                    self.on_event("wake", "F2")
                elif score >= config.WAKEWORD_THRESHOLD:
                    log.info("Wake word detectada (%.2f).", score)
                    self.on_event("wake", f"{score:.2f}")
                else:
                    continue
                oww.reset()
                self._beep()
                self.on_event("listening", "")
                audio = self._record_utterance(stream)
                if audio is None:
                    self.on_event("nothing", "não ouvi nada depois do 'Hey Jarvis' — fale logo após o bipe")
                    continue
                try:
                    text = self._transcribe(whisper, audio)
                except Exception as e:
                    self.on_event("error", f"falha na transcrição: {e}")
                    continue
                if not text:
                    self.on_event("nothing", "o áudio não tinha fala reconhecível")
                    continue
                log.info("🗣  \"%s\"", text)
                self.on_event("heard", text)
                try:
                    self.on_command(text)
                except Exception:
                    log.exception("Erro no callback de voz")

    def say(self, text: str) -> None:
        """Fala (bloqueante) com o microfone silenciado."""
        spoken = speakable(text)
        if not spoken:
            return
        self._speaking.set()
        try:
            self.tts.speak(spoken)
        except Exception as e:
            log.error("TTS falhou: %s", e)
        finally:
            time.sleep(0.2)
            self._speaking.clear()

    def listen_now(self) -> None:
        """Push-to-talk: começa a gravar o próximo comando imediatamente (sem wake word)."""
        if not self._speaking.is_set():
            self._ptt.set()

    def stop(self) -> None:
        self._stop_evt.set()


def diagnose(seconds: float = 6.0, on_tick: Callable[[float, float, float], None] | None = None) -> dict:
    """/voice test — grava `seconds` do microfone e mede tudo que importa para a voz funcionar:
    nível do microfone, nota da wake word ("Hey Jarvis") e a transcrição do Whisper.
    on_tick(segundos_decorridos, rms_atual, nota_wake) é chamado a cada 80 ms."""
    import numpy as np
    import sounddevice as sd

    eng = VoiceEngine(lambda t: None)
    oww = eng._load_wakeword()
    whisper = eng._load_whisper()
    mic = sd.query_devices(kind="input")["name"]
    frames, rms, best = [], [], 0.0
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
        n = int(seconds * SAMPLE_RATE / FRAME)
        for i in range(n):
            f = stream.read(FRAME)[0][:, 0]
            frames.append(f)
            r = eng._rms(f)
            rms.append(r)
            s = max(oww.predict(f).values() or [0.0])
            best = max(best, s)
            if on_tick:
                on_tick((i + 1) * FRAME / SAMPLE_RATE, r, s)
    audio = np.concatenate(frames)
    noise = float(np.percentile(rms, 20))
    peak = float(np.percentile(rms, 98))
    text = eng._transcribe(whisper, audio)
    problems = []
    if peak < SPEECH_FLOOR and text:
        problems.append(f"microfone baixo (pico {peak:.0f}): funciona, mas aumentar o volume de entrada em "
                        f"Configurações → Sistema → Som → {mic} deixa a detecção mais confiável")
    elif peak < SPEECH_FLOOR:
        problems.append(f"microfone muito baixo (pico {peak:.0f} < {SPEECH_FLOOR:.0f}): aumente o volume de "
                        f"entrada em Configurações → Sistema → Som → {mic}")
    if best < config.WAKEWORD_THRESHOLD:
        problems.append(f"'Hey Jarvis' não reconhecido (nota {best:.2f} < limiar {config.WAKEWORD_THRESHOLD}): "
                        f"pronuncie à americana 'Rêi DJÁR-vis' (J de 'Djalma') ou use a tecla F2 para falar")
    if not text:
        problems.append("o Whisper não reconheceu fala no áudio")
    return {"mic": mic, "noise": noise, "peak": peak, "wake": best, "threshold": config.WAKEWORD_THRESHOLD,
            "text": text, "problems": problems}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if len(sys.argv) > 2 and sys.argv[1] == "--say":
        PiperTTS().speak(" ".join(sys.argv[2:]))
    else:
        eng = VoiceEngine(lambda t: print(">>>", t))
        eng.start()
        try:
            while eng.is_alive():
                eng.join(0.5)
        except KeyboardInterrupt:
            eng.stop()
