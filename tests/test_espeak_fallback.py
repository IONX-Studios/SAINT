"""Linux fallback voice (modules/voice/espeak.py). Subprocess and audio are faked (mocked); a real
espeak-ng run happens only where espeak-ng is installed (marked `real`)."""

import io
import shutil

import numpy as np
import pytest

from modules.voice import espeak


def _wav(seconds=0.5, rate=22050):
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * rate), dtype=np.float32), rate, format="WAV")
    return buf.getvalue()


def test_render_is_resampled_to_24k_and_never_uses_a_shell(monkeypatch):
    calls = []

    class R:
        returncode, stdout, stderr = 0, _wav(), b""

    def fake_run(args, **kw):
        calls.append(args)
        assert "shell" not in kw
        return R()
    monkeypatch.setattr(espeak, "_exe", lambda: "/usr/bin/espeak-ng")
    monkeypatch.setattr(espeak.subprocess, "run", fake_run)
    audio = espeak._EspeakPipeline("en-us").render("-rf / is not an option", speed=1.2)
    assert abs(len(audio) - 12000) <= 1                         # 0.5 s at 24 kHz
    assert calls[0][-2:] == ["--", "-rf / is not an option"]    # text after "--": never a flag
    assert calls[0][calls[0].index("-s") + 1] == str(int(165 * 1.2))


def test_one_result_per_sentence(monkeypatch):
    p = espeak._EspeakPipeline()
    monkeypatch.setattr(p, "render", lambda s, speed=1.0: np.zeros(10, dtype=np.float32))
    assert [r.graphemes for r in p("Hello there. How are you?\nFine!")] == ["Hello there.", "How are you?", "Fine!"]


def test_failure_is_reported(monkeypatch):
    class R:
        returncode, stdout, stderr = 1, b"", b"voice not found"
    monkeypatch.setattr(espeak, "_exe", lambda: "/usr/bin/espeak-ng")
    monkeypatch.setattr(espeak.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="espeak-ng failed"):
        espeak._EspeakPipeline().render("hi")


@pytest.mark.real
@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")
def test_real_espeak_speaks():
    audio = espeak._EspeakPipeline().render("Hey SAINT, open Discord.")
    assert len(audio) > 24000 * 0.5 and float(np.abs(audio).max()) > 0.01
