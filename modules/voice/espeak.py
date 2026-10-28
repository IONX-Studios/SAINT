"""
modules/voice/espeak.py

The voice SAINT falls back to on Linux when Kokoro can't load: espeak-ng (the same
engine Kokoro already uses for out-of-dictionary words, and part of SAINTOS). The Linux
counterpart of modules/voice/sapi.py.

Each sentence is rendered by ``espeak-ng --stdout`` (a WAV in memory, no shell),
resampled to 24 kHz and played through KokoroTTS's own playback thread, so volume,
whisper replies, barge-in and interruption work exactly as with Kokoro.
"""

import io
import logging
import re
import shutil
import subprocess
from typing import Iterator

import numpy as np

from modules.voice.sapi import _Result
from modules.voice.tts import KokoroTTS

log = logging.getLogger("saint.tts.espeak")

_SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\n+")
_RATE = 24000
_BASE_WPM = 165


def _resample(audio: np.ndarray, src: int, dst: int = _RATE) -> np.ndarray:
    if src == dst or len(audio) == 0:
        return audio.astype(np.float32)
    n = int(round(len(audio) * dst / src))
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


def _exe() -> str:
    return shutil.which("espeak-ng") or ""


def available() -> bool:
    return bool(_exe())


class _EspeakPipeline:
    """KPipeline-shaped: ``pipeline(text, voice=..., speed=...)`` yields one result per sentence."""

    def __init__(self, voice: str = "en-us"):
        self._voice = voice or "en-us"

    def describe(self) -> str:
        res = subprocess.run([_exe(), "--version"], capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "espeak-ng --version failed")
        return res.stdout.strip().split(" Data")[0]

    def render(self, sentence: str, speed: float = 1.0) -> np.ndarray:
        import soundfile as sf
        wpm = max(80, min(450, int(_BASE_WPM * float(speed or 1.0))))
        # "--" ends the options, so a sentence starting with "-" is never read as a flag.
        res = subprocess.run([_exe(), "--stdout", "-v", self._voice, "-s", str(wpm), "--", sentence],
                             capture_output=True, timeout=30)
        if res.returncode != 0 or not res.stdout:
            raise RuntimeError(f"espeak-ng failed ({res.returncode}): {res.stderr[-200:]!r}")
        audio, rate = sf.read(io.BytesIO(res.stdout), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return _resample(audio, rate)

    def __call__(self, text: str, voice: str = "", speed: float = 1.0) -> Iterator[_Result]:
        for sentence in (s.strip() for s in _SENTENCE.split(text or "")):
            if sentence:
                yield _Result(sentence, self.render(sentence, speed))


class EspeakTTS(KokoroTTS):
    """KokoroTTS with espeak-ng's voice instead of the Kokoro model (Linux)."""

    def __init__(self, voice: str = "", speed: float = 1.0, reason: str = "", **_ignored):
        from core.config import config
        super().__init__(voice=voice, speed=speed, device="cpu", allow_cpu_fallback=True, require_cuda=False)
        self._espeak_voice = str(config.get("voice.espeak_voice", "") or "en-us")
        self._reason = reason or "Kokoro isn't available"

    def _load(self):
        if self._pipeline is not None:
            return
        with self._load_lock:
            if self._pipeline is not None:
                return
            try:
                pipe = _EspeakPipeline(self._espeak_voice)
                name = pipe.describe()               # proves espeak-ng runs here
                self._pipeline = pipe
                self._resolved_device = f"espeak-ng ({name})"
                log.info("tts.espeak.ready %s (%s)", name, self._reason)
            except Exception as e:
                self._load_error = RuntimeError(f"espeak-ng isn't available: {e}")
                raise self._load_error

    @property
    def device_info(self) -> dict:
        return {"requested_device": "kokoro", "resolved_device": self._resolved_device, "cuda_available": False,
                "fell_back": True, "reason": self._reason}

    def warm_up(self):
        try:
            self._load()
        except Exception:
            log.debug("tts.espeak.warm_up_failed", exc_info=True)

    def _voice_for(self, code: str):
        return self._pipeline, self._voice

    def _chain(self, text: str):
        yield from self._pipeline(text, voice=self._voice, speed=self._speed)
