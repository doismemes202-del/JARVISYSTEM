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
class VoiceEngine(threading.Thread):
    def __init__(self, on_command: Callable[[str], None]) -> None:
        super().__init__(daemon=True, name="voice-engine")
        self.on_command = on_command
        self.tts = PiperTTS()
        self._stop_evt = threading.Event()
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
        threshold = max(self._noise * 3.0, 450.0)
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
            return

        log.info("🎙  Escuta ativa. Diga '%s'.", config.WAKEWORD_MODEL.replace("_", " "))
        self.ready.set()
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
            while not self._stop_evt.is_set():
                frame = stream.read(FRAME)[0][:, 0]
                if self._speaking.is_set():
                    continue                       # evita o Jarvis ouvir a própria voz
                self._noise = 0.95 * self._noise + 0.05 * self._rms(frame)
                scores = oww.predict(frame)
                if not scores or max(scores.values()) < config.WAKEWORD_THRESHOLD:
                    continue

                log.info("Wake word detectada (%.2f).", max(scores.values()))
                oww.reset()
                self._beep()
                audio = self._record_utterance(stream)
                if audio is None:
                    log.info("Nenhuma fala após a wake word.")
                    continue
                try:
                    text = self._transcribe(whisper, audio)
                except Exception as e:
                    log.error("Falha na transcrição: %s", e)
                    continue
                if text:
                    log.info("🗣  \"%s\"", text)
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

    def stop(self) -> None:
        self._stop_evt.set()


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
