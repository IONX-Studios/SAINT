"""
ui/sounds.py

SAINT's own sounds on Halo: the overlay opens with a rising star chime and closes with a falling one
(the "saint" sound theme, packaging/linux/branding/make_sounds.py). Played with PipeWire's pw-play
(or PulseAudio's paplay), in the background, never blocking the UI. Off with Settings › Appearance ›
"Sounds" (appearance.sounds); silent where the theme isn't installed (Windows, a source checkout).
"""

import shutil
import subprocess
from pathlib import Path

from core.config import config

THEME = Path("/usr/share/sounds/saint/stereo")


def play(name: str) -> bool:
    if not config.get("appearance.sounds", True):
        return False
    wav = THEME / f"{name}.wav"
    player = shutil.which("pw-play") or shutil.which("paplay")
    if not player or not wav.is_file():
        return False
    try:
        subprocess.Popen([player, str(wav)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False
