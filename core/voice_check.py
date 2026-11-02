"""
core/voice_check.py

What SAINT's voice really is, and tests that prove it — for the Halo Voice page and the dashboard.

``status()`` reads files and the running TTS service, never the config alone: the configured backend is
reported next to the engine that actually speaks, so "kokoro" in the config can't hide espeak-ng.
``test_kokoro()`` synthesises a phrase with Kokoro (onnxruntime) and plays it; ``test_whisper()`` records
the microphone (or takes given audio) and transcribes it with faster-whisper. A result only says "ok"
when that actually happened; the last results are kept in ``voice_tests.json`` in SAINT's data folder.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, Optional

KOKORO_RATE = 24000
WHISPER_RATE = 16000
TEST_PHRASE = "Hello, I'm SAINT. This is my Kokoro voice."


def _results_file() -> Path:
    from core.paths import data_dir
    return data_dir() / "voice_tests.json"


def last_results() -> Dict[str, dict]:
    try:
        return json.loads(_results_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record(name: str, ok: bool, detail: str) -> dict:
    res = {"ok": ok, "detail": detail, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    data = last_results()
    data[name] = res
    try:
        _results_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
    return res


def _audio_backend() -> str:
    try:
        import sounddevice as sd
        api = sd.query_hostapis(sd.default.hostapi)["name"] if sd.default.hostapi >= 0 else "none"
        out = sd.query_devices(kind="output")["name"]
        return f"{api}: {out}"
    except Exception as e:
        return f"unavailable ({type(e).__name__}: {e})"


def _microphone() -> str:
    try:
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"]
    except Exception as e:
        return f"none ({type(e).__name__}: {e})"


def status(service=None) -> Dict[str, str]:
    """The voice system as it is now. ``service`` is the running TTSService when SAINT's own process asks."""
    from core.config import config
    from modules.voice import kokoro_onnx
    model = kokoro_onnx.model_dir()
    have_model = kokoro_onnx.available()
    try:
        import importlib.util
        runtime = "onnxruntime" if importlib.util.find_spec("onnxruntime") else "onnxruntime missing"
    except Exception:
        runtime = "unknown"
    if service is not None:
        engine = getattr(service, "_engine_type", None) or "not started"
        if engine == "kokoro":
            info = getattr(service, "_device_info", {}) or {}
            engine = f"Kokoro ({info.get('device') or info.get('reason') or 'loaded'})"
        elif engine in ("espeak", "sapi"):
            engine = f"{'espeak-ng' if engine == 'espeak' else 'Windows voice'} — NOT Kokoro (fallback)"
    else:
        engine = "Kokoro ready to load" if have_model else "Kokoro model missing — SAINT would fall back to espeak-ng"
    try:
        from core import model_assets
        whisper = "faster-whisper base.en (downloaded)" if model_assets.whisper_ready() else \
            "faster-whisper base.en (not downloaded yet)"
    except Exception as e:
        whisper = f"unknown ({e})"
    tests = last_results()

    def last(name):
        t = tests.get(name)
        return "never run" if not t else f"{'passed' if t['ok'] else 'FAILED'} {t['at']}: {t['detail']}"
    return {
        "Configured voice": f"{config.get('voice.tts_backend', 'kokoro')} / {config.get('voice.tts_voice', 'af_heart')}",
        "Speaking with": engine,
        "Kokoro model": f"{model} ({'present' if have_model else 'missing'})",
        "Runtime": runtime,
        "Audio output": _audio_backend(),
        "Microphone": _microphone(),
        "Speech recognition": whisper,
        "Last Kokoro test": last("kokoro"),
        "Last Whisper test": last("whisper"),
    }


def test_kokoro(play: bool = True, download: bool = True) -> dict:
    """Synthesise TEST_PHRASE with Kokoro and play it. Never uses another engine."""
    try:
        from core import model_assets
        from modules.voice import kokoro_onnx
        if not kokoro_onnx.available():
            if not download:
                return _record("kokoro", False, f"Kokoro model files missing in {kokoro_onnx.model_dir()}")
            model_assets.ensure_kokoro()
            if not kokoro_onnx.available():
                return _record("kokoro", False, "Kokoro model files missing and the download didn't produce them")
        import numpy as np
        from core.config import config
        voice = config.get("voice.tts_voice", "af_heart") or "af_heart"
        tts = kokoro_onnx.KokoroOnnxTTS(voice=voice)
        tts._load()
        t0 = time.monotonic()
        chunks = [r.audio for r in tts._pipeline(TEST_PHRASE, voice=voice, speed=1.0)]
        synth = time.monotonic() - t0
        audio = np.concatenate([np.asarray(c, dtype="float32").reshape(-1) for c in chunks])
        secs = len(audio) / KOKORO_RATE
        if secs < 0.5 or float(np.abs(audio).max()) < 1e-3:
            return _record("kokoro", False, f"Kokoro produced no audible speech ({secs:.2f}s)")
        detail = f"voice {voice}: {secs:.1f}s of speech in {synth:.2f}s at {KOKORO_RATE} Hz"
        if play:
            import sounddevice as sd
            sd.play(audio, KOKORO_RATE)
            sd.wait()
            detail += f", played on {_audio_backend()}"
        return _record("kokoro", True, detail)
    except Exception as e:
        return _record("kokoro", False, f"{type(e).__name__}: {e}")


def test_whisper(audio=None, seconds: float = 4.0, expect: str = "",
                 on_status: Optional[Callable[[str], None]] = None) -> dict:
    """Transcribe ``audio`` (float32 at 16 kHz, -1..1) or ``seconds`` recorded from the microphone."""
    say = on_status or (lambda _t: None)
    try:
        import numpy as np
        if audio is None:
            import sounddevice as sd
            say(f"Recording {seconds:.0f} seconds — say something…")
            audio = sd.rec(int(seconds * WHISPER_RATE), samplerate=WHISPER_RATE, channels=1, dtype="float32")
            sd.wait()
            audio = audio.reshape(-1)
            if float(np.abs(audio).max()) < 1e-3:
                return _record("whisper", False, f"the microphone ({_microphone()}) recorded silence")
        say("Transcribing…")
        from modules.voice.stt import FasterWhisperSTT
        stt = FasterWhisperSTT(model_name="base.en", device="cpu", compute_type="int8")
        # transcribe() takes microphone-scale samples (int16 range), like the audio thread gives it.
        res = stt.transcribe(np.concatenate([audio, np.zeros(8000, dtype="float32")]) * 32767.0)
        text = (res.text or "").strip()
        if not text:
            return _record("whisper", False, "Whisper heard nothing in the recording")
        if expect and expect.lower() not in text.lower():
            return _record("whisper", False, f"heard {text!r}, expected {expect!r}")
        return _record("whisper", True, f"heard {text!r}")
    except Exception as e:
        return _record("whisper", False, f"{type(e).__name__}: {e}")
