"""
core/health.py

Real checks for the dashboard: each answers from the running system, never from the config alone.
Results use one vocabulary — working, degraded, missing, failed, not tested — so every page shows the
same words for the same facts.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional

WORKING, DEGRADED, MISSING, FAILED, NOT_TESTED = "working", "degraded", "missing", "failed", "not tested"


def _result(state: str, detail: str, **extra) -> Dict:
    return {"state": state, "detail": detail, "at": time.strftime("%H:%M:%S"), **extra}


def ollama(base_url: str, model: str, get: Optional[Callable] = None) -> Dict:
    """Is Ollama answering, and is the configured model actually downloaded? (No inference: cheap enough to
    run every few seconds.)"""
    import shutil
    get = get or _get
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    try:
        tags = get(base + "/api/tags")
    except Exception as e:
        if not shutil.which("ollama"):
            return _result(MISSING, "Ollama isn't installed")
        return _result(FAILED, f"Ollama is installed but not answering ({type(e).__name__})")
    names = [m.get("name", "") for m in (tags or {}).get("models", [])]
    if not names:
        return _result(DEGRADED, "Ollama is running but has no models", models=names)
    want = (model or "").strip()
    if want and not any(n == want or n.split(":")[0] == want or n == f"{want}:latest" for n in names):
        return _result(DEGRADED, f"model {want} isn't downloaded (have: {', '.join(names[:4])})", models=names)
    return _result(WORKING, f"{want or names[0]} downloaded", models=names)


def ollama_inference(base_url: str, model: str, post: Optional[Callable] = None, timeout: float = 120) -> Dict:
    """Send one real prompt and check a non-empty answer comes back."""
    post = post or _post
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    t0 = time.monotonic()
    try:
        res = post(base + "/api/generate", {"model": model, "prompt": "Reply with the single word: ready",
                                            "stream": False, "options": {"num_predict": 8}}, timeout)
    except Exception as e:
        return _result(FAILED, f"inference failed: {type(e).__name__}: {e}"[:300])
    text = ((res or {}).get("response") or "").strip()
    if not text:
        return _result(FAILED, "the model answered with nothing")
    return _result(WORKING, f"{model} answered {text[:40]!r} in {time.monotonic() - t0:.1f}s")


def tts(service) -> Dict:
    """What the running TTS service really speaks with."""
    if service is None:
        return _result(NOT_TESTED, "voice not started")
    engine = getattr(service, "_engine_type", "") or ""
    if not engine:
        err = getattr(service, "_last_error", None) or getattr(service, "_load_error", None)
        return _result(FAILED if err else NOT_TESTED, str(err) if err else "voice not loaded yet", engine="")
    if engine == "kokoro":
        fell_back = bool((getattr(service, "_device_info", {}) or {}).get("fell_back"))
        return _result(DEGRADED if fell_back else WORKING, "Kokoro" + (" on CPU fallback" if fell_back else ""),
                       engine=engine)
    if engine in ("espeak", "sapi"):
        return _result(DEGRADED, f"{'espeak-ng' if engine == 'espeak' else 'Windows voice'} — Kokoro isn't loaded",
                       engine=engine)
    return _result(WORKING, engine, engine=engine)


def _get(url: str, timeout: float = 2.0):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(url: str, body: dict, timeout: float):
    import requests
    r = requests.post(url, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()
