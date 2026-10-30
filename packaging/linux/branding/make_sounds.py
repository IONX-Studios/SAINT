"""
packaging/linux/branding/make_sounds.py

Generates SAINT's sound theme from code (nothing binary is committed; the sounds are original):
a freedesktop sound theme "saint" with the event names Plasma and apps play, plus SAINT's own.

    python packaging/linux/branding/make_sounds.py OUTPUT_DIR      -> OUTPUT_DIR/saint/{index.theme,stereo/*.wav}

Bell-like tones: a few sine partials with an exponential decay, 44.1 kHz 16-bit stereo, soft (peak -6 dB).
Deterministic; standard library only.
"""

import math
import struct
import sys
import wave
from pathlib import Path

RATE = 44100

# Notes (Hz) used below.
C5, D5, E5, G5, A5, C6, E6, G6 = 523.25, 587.33, 659.25, 783.99, 880.0, 1046.5, 1318.5, 1568.0
A3, C4, E4, G4 = 220.0, 261.63, 329.63, 392.0


def render(notes, length):
    """notes: (freq Hz, start s, decay s, gain); returns float samples."""
    n = int(RATE * length)
    out = [0.0] * n
    for freq, start, decay, gain in notes:
        s0 = int(start * RATE)
        for i in range(s0, n):
            t = (i - s0) / RATE
            env = math.exp(-t / decay) * min(1.0, t * 400)          # 2.5 ms attack: no click
            if env < 1e-4:
                break
            # A bell: the fundamental plus two quieter, slightly inharmonic partials.
            out[i] += gain * env * (math.sin(2 * math.pi * freq * t)
                                    + 0.35 * math.sin(2 * math.pi * freq * 2.01 * t)
                                    + 0.12 * math.sin(2 * math.pi * freq * 3.03 * t))
    peak = max(1e-9, max(abs(x) for x in out))
    return [x / peak * 0.5 for x in out]                            # -6 dB


def write(path: Path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<hh", int(x * 32767), int(x * 32767)) for x in samples))


def arpeggio(freqs, step, decay, length, gain=1.0):
    return render([(f, i * step, decay, gain) for i, f in enumerate(freqs)], length)


SOUNDS = {
    # SAINT
    "saint-wake": lambda: arpeggio([G5, C6], 0.07, 0.25, 0.6),                    # heard you
    "saint-done": lambda: arpeggio([C6, G5], 0.08, 0.3, 0.7),                     # finished
    "saint-star-open": lambda: arpeggio([C5, E5, G5, C6, E6], 0.035, 0.22, 0.75, 0.8),
    "saint-star-close": lambda: arpeggio([E6, C6, G5, E5, C5], 0.035, 0.18, 0.65, 0.8),
    # freedesktop / Plasma events
    "desktop-login": lambda: arpeggio([C4, G4, C5, E5, G5], 0.11, 0.9, 2.0),
    "desktop-logout": lambda: arpeggio([G5, E5, C5, G4, C4], 0.11, 0.8, 1.8),
    "message-new-instant": lambda: arpeggio([E6, G6], 0.09, 0.2, 0.6),
    "message-new-email": lambda: arpeggio([C6, E6], 0.1, 0.25, 0.7),
    "dialog-information": lambda: arpeggio([A5, E6], 0.08, 0.25, 0.7),
    "dialog-warning": lambda: arpeggio([E5, E5], 0.16, 0.18, 0.7),
    "dialog-error": lambda: render([(A3, 0, 0.3, 1.0), (A3 * 1.06, 0, 0.3, 0.8)], 0.7),
    "device-added": lambda: arpeggio([G5, D5 * 2], 0.07, 0.18, 0.5),
    "device-removed": lambda: arpeggio([D5 * 2, G5], 0.07, 0.18, 0.5),
    "power-plug": lambda: arpeggio([C5, G5], 0.07, 0.2, 0.5),
    "power-unplug": lambda: arpeggio([G5, C5], 0.07, 0.2, 0.5),
    "window-attention": lambda: arpeggio([A5, A5], 0.12, 0.15, 0.5),
    "complete": lambda: arpeggio([C6, E6, G6], 0.06, 0.25, 0.8),
    "outcome-success": lambda: arpeggio([C6, E6, G6], 0.06, 0.25, 0.8),
    "camera-shutter": lambda: render([(G6 * 2, 0, 0.02, 1.0), (E6 * 2, 0.06, 0.03, 0.8)], 0.2),
    "screen-capture": lambda: render([(G6 * 2, 0, 0.02, 1.0), (E6 * 2, 0.06, 0.03, 0.8)], 0.2),
    "bell": lambda: render([(A5, 0, 0.4, 1.0)], 1.0),
    "audio-volume-change": lambda: render([(E6, 0, 0.05, 1.0)], 0.15),
}

INDEX = """[Sound Theme]
Name=SAINT
Comment=SAINT's sounds: soft bells in SAINT's own voice
Inherits=freedesktop
Directories=stereo

[stereo]
OutputProfile=stereo
"""


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    theme = Path(argv[1]) / "saint"
    (theme / "stereo").mkdir(parents=True, exist_ok=True)
    (theme / "index.theme").write_text(INDEX, encoding="utf-8")
    for name, make in SOUNDS.items():
        write(theme / "stereo" / f"{name}.wav", make())
    print(f"{len(SOUNDS)} sounds written to {theme}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
