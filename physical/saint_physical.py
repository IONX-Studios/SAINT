#!/usr/bin/env python3
"""
assistantMainPi.py - SAINT single-file assistant (enhanced & fixed)

Merged single-file version including:
- timers + separate countdown window
- reminders scheduling + persistence
- notifications (plyer preferred, fallback to tkinter/console)
- conversational fallback when no explicit command parsed
- all original functionality preserved
"""
from __future__ import annotations
import os
import sys
import time
import threading
import json
import math
import queue
import re
import random
import shutil
import subprocess
import traceback
import base64
import urllib.parse
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, Dict

# network + parsing libs
import requests
import dateparser
import wikipedia

# audio + model libs (optional)
try:
    import sounddevice as sd
except Exception:
    sd = None
try:
    import numpy as np
except Exception:
    np = None
try:
    import onnxruntime as ort
except Exception:
    ort = None
try:
    import pyttsx3
    HAS_TTS = True
except Exception:
    pyttsx3 = None
    HAS_TTS = False
try:
    import speech_recognition as sr
    HAS_SR = True
except Exception:
    sr = None
    HAS_SR = False

# notification library (preferred)
try:
    from plyer import notification as plyer_notification
except Exception:
    plyer_notification = None

# hardware optional
try:
    import pigpio
    PIGPIO_AVAILABLE = True
except Exception:
    pigpio = None
    PIGPIO_AVAILABLE = False

# llama-cpp (optional)
try:
    from llama_cpp import Llama
    HAS_LLAMA_CPP = True
except Exception:
    Llama = None
    HAS_LLAMA_CPP = False

# Kokoro neural TTS (optional; falls back to pyttsx3 / espeak-ng)
try:
    from kokoro_onnx import Kokoro
except Exception:
    Kokoro = None

# picovoice/rhino optional
try:
    import pvrhino
    HAS_RHINO = True
except Exception:
    pvrhino = None
    HAS_RHINO = False

# GUI & web callback
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, scrolledtext
from flask import Flask, request

# ------------ Configuration ------------
_HERE = os.path.dirname(os.path.abspath(__file__))

def _secret(name: str, default: str = "") -> str:
    """Secrets come from the environment or saint_secrets.json next to this script, never from the source."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        with open(os.path.join(_HERE, "saint_secrets.json"), "r", encoding="utf-8") as f:
            return str(json.load(f).get(name, default))
    except Exception:
        return default
WAKE_MODEL_PATH = os.environ.get("WAKE_MODEL_PATH", os.path.join(_HERE, "WakeWordCreation", "trained_model", "wake_model.onnx"))
WAKE_SAMPLE_RATE = int(os.environ.get("WAKE_SAMPLE_RATE", "16000"))
WAKE_WINDOW_MS = int(os.environ.get("WAKE_WINDOW_MS", "1000"))
WAKE_THRESHOLD = float(os.environ.get("WAKE_THRESHOLD", "0.6"))
WAKE_COOLDOWN = float(os.environ.get("WAKE_COOLDOWN", "1.2"))

MODEL_PATH = os.environ.get("MODEL_PATH", os.path.join(_HERE, "models", "ModelFiles", "llama-3.2-1b-instruct-q4_k_m.gguf"))

# TTS: auto = Kokoro if its model files exist, else pyttsx3, else espeak-ng
TTS_ENGINE = os.environ.get("SAINT_TTS", "auto").lower()   # auto | kokoro | pyttsx3 | espeak | print
KOKORO_MODEL_PATH = os.environ.get("KOKORO_MODEL", os.path.join(_HERE, "models", "kokoro", "kokoro-v1.0.int8.onnx"))
KOKORO_VOICES_PATH = os.environ.get("KOKORO_VOICES", os.path.join(_HERE, "models", "kokoro", "voices-v1.0.bin"))
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "am_michael")
KOKORO_SPEED = float(os.environ.get("KOKORO_SPEED", "1.05"))
KOKORO_LANG = os.environ.get("KOKORO_LANG", "en-us")
SAINT_TOKEN = _secret("SAINT_TOKEN")

INVERT_PAN = False
INVERT_TILT = False
INVERT_EYES_LR = False
INVERT_EYES_UD = False

RHINO_CONTEXT_PATH = os.environ.get("RHINO_CONTEXT", os.path.join(_HERE, "models", "RhinoContext", "RhinoContext.rhn"))
PICOVOICE_ACCESS_KEY = _secret("PICOVOICE_ACCESS_KEY")

PINS = {
    "mouth": 17,
    "eyes_ud": 18,
    "eyes_lr": 27,
    "tilt": 22,
    "pan": 23,
    "rgb_r": 24,
    "rgb_g": 25,
    "rgb_b": 26,
}
COMMON_ANODE = False

SERVO_CONFIG = {
    "mouth": {"min": 600, "max": 2400, "angle_min": 90, "angle_max": 130},
    "eyes_ud": {"min": 600, "max": 2400, "angle_min": 40, "angle_max": 90},
    "eyes_lr": {"min": 600, "max": 2400, "angle_min": 55, "angle_max": 125},
    "tilt": {"min": 500, "max": 2500, "angle_min": 30, "angle_max": 100},
    "pan": {"min": 500, "max": 2500, "angle_min": 30, "angle_max": 150},
}

ANGLE_MIN = 0.0
ANGLE_MAX = 180.0

# ------------ Motion safety + Demo Mode configuration ------------
# Calibration note: angle_min/angle_max above are absolute degrees in the same
# ANGLE_MIN..ANGLE_MAX frame that set_angle maps linearly onto each servo's
# min..max pulse (e.g. pan 30..150 deg -> ~833..2167 us). They are the HARD
# limits: no voice command, slider or demo can go outside them. Pins and pulse
# widths are unchanged; the degrees are nominal until measured on the robot.
HARD_PULSE_MIN = 500
HARD_PULSE_MAX = 2500

# neutral: rest pose for Center / emergency stop / shutdown.
# demo_min..demo_max: the only range Demo Mode may use (inside the hard limits).
# slew_deg_s: max speed of ANY move of that servo (None = unlimited).
# CALIBRATE neutral and the demo ranges on the real robot before enabling the neck.
SERVO_SAFETY = {
    "mouth":   {"neutral": 90, "demo_min": 90, "demo_max": 90,  "slew_deg_s": None},
    "eyes_ud": {"neutral": 65, "demo_min": 57, "demo_max": 73,  "slew_deg_s": None},
    "eyes_lr": {"neutral": 90, "demo_min": 75, "demo_max": 105, "slew_deg_s": None},
    "tilt":    {"neutral": 90, "demo_min": 84, "demo_max": 96,  "slew_deg_s": 30.0},
    "pan":     {"neutral": 90, "demo_min": 72, "demo_max": 108, "slew_deg_s": 30.0},
}
SLEW_TICK_S = 0.02            # interval between interpolated steps (max step = slew_deg_s * SLEW_TICK_S)
NEUTRAL_RETURN_DEG_S = 20.0   # speed for Center / E-stop / shutdown return
LOOK_STEP_DEG = {"eyes": 20, "head": 15}  # "look left" without degrees moves this far, not to the end stop

def _env_flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")

# --simulate / SAINT_SIMULATE=1: never create a pigpio connection or send pigpio commands
SIMULATE = "--simulate" in sys.argv or _env_flag("SAINT_SIMULATE")

DEMO_CONFIG = {
    # head (pan/tilt) moves in Demo Mode by default, inside the narrow demo ranges above.
    # turn it off with --no-demo-neck or SAINT_DEMO_NECK=0
    "neck_enabled": "--no-demo-neck" not in sys.argv and _env_flag("SAINT_DEMO_NECK", True),
    "tick_s": 0.02,
    "eye_speed_deg_s": 60.0,
    "neck_speed_deg_s": 10.0,     # also capped by slew_deg_s
    "tempo": 1.0,                 # >1 slower, <1 faster choreography
    "led_fade_s": 1.2,            # mood colour cross-fade
    "intro_line": os.environ.get("SAINT_DEMO_INTRO", "Demo mode. Hi, I'm SAINT."),
}

def validate_safety_config() -> None:
    """Refuse to start with a demo range or neutral outside a servo's hard limits."""
    for name, cfg in SERVO_CONFIG.items():
        s = SERVO_SAFETY.get(name)
        if s is None:
            raise ValueError(f"SERVO_SAFETY missing entry for {name}")
        lo = max(float(cfg.get("angle_min", ANGLE_MIN)), ANGLE_MIN)
        hi = min(float(cfg.get("angle_max", ANGLE_MAX)), ANGLE_MAX)
        if not (lo <= s["demo_min"] <= s["neutral"] <= s["demo_max"] <= hi):
            raise ValueError(f"{name}: need {lo} <= demo_min <= neutral <= demo_max <= {hi}, got {s}")
        if s["slew_deg_s"] is not None and s["slew_deg_s"] <= 0:
            raise ValueError(f"{name}: slew_deg_s must be > 0 or None")
        if not (HARD_PULSE_MIN <= cfg["min"] < cfg["max"] <= HARD_PULSE_MAX):
            raise ValueError(f"{name}: pulse range {cfg['min']}..{cfg['max']} outside {HARD_PULSE_MIN}..{HARD_PULSE_MAX}")

validate_safety_config()

# ---- Physical version: lip-sync mouth + ambient look-around ----
# Mouth: closed/open angles (inside the mouth's hard range 90..130). The mouth follows
# the loudness of the speech: smoothed so it never snaps, and driven from the real
# audio samples when Kokoro is the voice (otherwise a syllable-rate flap while talking).
MOUTH_CONFIG = {
    "closed": float(os.environ.get("SAINT_MOUTH_CLOSED", "90")),
    "open": float(os.environ.get("SAINT_MOUTH_OPEN", "115")),
    "tick_s": 0.025,          # 40 Hz update
    "attack_s": 0.045,        # time to open (smoothing time constant)
    "release_s": 0.09,        # time to close
    "max_deg_s": 450.0,       # never ask the servo for more than this speed
    "min_step_deg": 1.0,      # ignore changes smaller than this (stops buzzing)
    "audio_lead_s": float(os.environ.get("SAINT_MOUTH_LEAD", "0.04")),  # +ve = mouth moves earlier
    "frame_s": 0.02,          # envelope resolution
    "syllable_hz": 4.5,       # fallback flap rate when no audio samples exist
}

# Ambient gaze: idle glances + subtle gaze shifts while talking. Wider than the demo
# range but still well inside each servo's hard limits. Neck speed is also capped by slew_deg_s.
GAZE_ENABLED = "--no-gaze" not in sys.argv and _env_flag("SAINT_GAZE", True)
GAZE_RANGE = {  # name: (min_deg, max_deg)
    "eyes_lr": (70, 110), "eyes_ud": (58, 72),
    "pan": (68, 112),     "tilt": (85, 95),
}
GAZE_CONFIG = {
    "eye_speed_deg_s": 140.0, "neck_speed_deg_s": 22.0, "tick_s": 0.02,
    "idle_wait_s": (2.5, 7.0),   # time between idle glances
    "talk_wait_s": (1.6, 3.8),   # time between gaze shifts while speaking
    "head_follow_prob": 0.65,    # idle glance also turns the head
    "talk_scale": 0.45,          # fraction of the range used while speaking
    "yield_s": 6.0,              # stay out of the way this long after a manual command
}

def validate_gaze_config() -> None:
    for name, (lo, hi) in GAZE_RANGE.items():
        s, cfg = SERVO_SAFETY[name], SERVO_CONFIG[name]
        if not (cfg["angle_min"] <= lo <= s["neutral"] <= hi <= cfg["angle_max"]):
            raise ValueError(f"GAZE_RANGE {name}: {lo}..{hi} must contain neutral {s['neutral']} and stay inside the hard limits")
    lo, hi = SERVO_CONFIG["mouth"]["angle_min"], SERVO_CONFIG["mouth"]["angle_max"]
    if not (lo <= MOUTH_CONFIG["closed"] < MOUTH_CONFIG["open"] <= hi):
        raise ValueError(f"MOUTH_CONFIG closed/open must satisfy {lo} <= closed < open <= {hi}")

validate_gaze_config()

SPOTIFY_CLIENT_ID = _secret("SPOT_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = _secret("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8765/spotify/callback"
SPOTIFY_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REMINDERS_DB = os.path.join(BASE_DIR, "reminders.json")
HISTORY_DB = os.path.join(BASE_DIR, "conversation_history.json")

# ------------ Globals (runtime) ------------
CONTROLLER = None
AGENT = None
SPOT = None
RHINO = None
llm = None
CURRENT_GUI = None
DEMO = None
GAZE = None

REMINDERS: Dict[str, Dict[str, Any]] = {}
REMINDERS_LOCK = threading.RLock()   # schedule_reminder holds it while persist_reminders re-takes it (was a deadlock)

CONVERSATION_HISTORY: list = []
HISTORY_LOCK = threading.Lock()

MUTE_MIC = False
MUTE_SPEAKER = False

HEADERS = {"User-Agent": "SAINT-Assistant/1.0 (contact:none@example.com)"}

# ------------ Utilities: history ------------
def save_history_entry(role: str, text: str) -> None:
    global CONVERSATION_HISTORY
    entry = {"role": role, "text": text, "time": datetime.utcnow().isoformat() + "Z"}
    with HISTORY_LOCK:
        CONVERSATION_HISTORY.append(entry)
        try:
            trimmed = CONVERSATION_HISTORY[-1000:]
            with open(HISTORY_DB, "w", encoding="utf-8") as f:
                json.dump(trimmed, f, indent=2)
        except Exception as e:
            print("[HISTORY_SAVE_ERR]", e)

def load_history() -> None:
    global CONVERSATION_HISTORY
    try:
        if os.path.exists(HISTORY_DB):
            with open(HISTORY_DB, "r", encoding="utf-8") as f:
                CONVERSATION_HISTORY = json.load(f)
    except Exception:
        CONVERSATION_HISTORY = []

# ------------ ServoController (pigpio optional, otherwise simulated) ------------
class ServoController:
    """Servo + eye LED driver. Every angle goes through clamp_angle (hard limits);
    servos with slew_deg_s are interpolated so they never jump. In simulate mode
    (or without a pigpio daemon) nothing is sent to hardware; command_log records
    what would have been sent."""
    def __init__(self, simulate: bool = False):
        self.simulate = bool(simulate)
        self.pi = None
        if PIGPIO_AVAILABLE and not self.simulate:
            try:
                self.pi = pigpio.pi()
                if not self.pi.connected:
                    self.pi = None
            except Exception:
                self.pi = None
        if self.pi is None:
            print("[SERVO] " + ("SIMULATION mode: no pigpio commands will be sent" if self.simulate
                                else "pigpio not connected; servo/LED commands are simulated"))
        self.state = {name: {"angle": None, "pulse": 0} for name in SERVO_CONFIG}
        self.rgb_state = {"on": False, "r": 0, "g": 0, "b": 0, "last": (255,255,255)}
        self.rgb_pins = {"r": PINS["rgb_r"], "g": PINS["rgb_g"], "b": PINS["rgb_b"]}
        self._lock = threading.RLock()       # one motion at a time
        self._abort = threading.Event()      # set by abort_motion() to cut a slew short
        self.command_log = deque(maxlen=50000)  # ("servo", name, angle, pulse) / ("rgb", r, g, b)

    def hard_range(self, name: str):
        cfg = SERVO_CONFIG[name]
        return (max(float(cfg.get("angle_min", ANGLE_MIN)), ANGLE_MIN),
                min(float(cfg.get("angle_max", ANGLE_MAX)), ANGLE_MAX))

    def clamp_angle(self, name: str, angle: float) -> float:
        if name not in SERVO_CONFIG:
            raise ValueError(f"Unknown servo {name}")
        a = float(angle)
        if not math.isfinite(a):
            raise ValueError(f"Invalid angle {angle!r} for {name}")
        lo, hi = self.hard_range(name)
        return max(lo, min(hi, a))

    def _write(self, name: str, a: float) -> int:
        """Send an already-clamped angle. Pulse is clamped again as a last line of defence."""
        cfg = SERVO_CONFIG[name]
        pulse = int(cfg["min"] + (cfg["max"] - cfg["min"]) * (a - ANGLE_MIN) / (ANGLE_MAX - ANGLE_MIN))
        pulse = max(cfg["min"], HARD_PULSE_MIN, min(cfg["max"], HARD_PULSE_MAX, pulse))
        if self.pi:
            try:
                self.pi.set_servo_pulsewidth(PINS.get(name), pulse)
            except Exception as e:
                print(f"[SERVO] {name} write failed: {e}")
        self.state[name]["angle"] = a
        self.state[name]["pulse"] = pulse
        self.command_log.append(("servo", name, a, pulse))
        return pulse

    def set_angle(self, name: str, angle: float) -> int:
        a = self.clamp_angle(name, angle)
        slew = SERVO_SAFETY[name]["slew_deg_s"]
        with self._lock:
            cur = self.state[name]["angle"]
            if slew and cur is None:
                # position unknown after boot: settle at neutral first, then slew to the target
                self._write(name, self.clamp_angle(name, SERVO_SAFETY[name]["neutral"]))
                cur = self.state[name]["angle"]
            if slew and abs(a - cur) > slew * SLEW_TICK_S:
                self.move_many({name: a}, slew)
                return self.state[name]["pulse"]
            return self._write(name, a)

    def move_many(self, targets: Dict[str, float], deg_per_s, stop_event: Optional[threading.Event] = None) -> bool:
        """Move several servos together toward targets at deg_per_s (float or {name: speed}),
        never faster than each servo's slew limit. Returns False if interrupted by
        stop_event or abort_motion(); servos then hold where they are."""
        with self._lock:
            goals = {n: self.clamp_angle(n, t) for n, t in targets.items()}
            steps = {}
            for n, goal in goals.items():
                speed = deg_per_s[n] if isinstance(deg_per_s, dict) else deg_per_s
                slew = SERVO_SAFETY[n]["slew_deg_s"]
                if slew:
                    speed = min(speed, slew)
                steps[n] = max(float(speed), 0.1) * SLEW_TICK_S
                if self.state[n]["angle"] is None:
                    # unknown start: begin from neutral (slewed servos) or go straight there
                    self._write(n, self.clamp_angle(n, SERVO_SAFETY[n]["neutral"]) if slew else goal)
            while True:
                remaining = False
                for n, goal in goals.items():
                    cur = self.state[n]["angle"]
                    if abs(goal - cur) <= steps[n]:
                        if cur != goal:
                            self._write(n, goal)
                    else:
                        self._write(n, cur + (steps[n] if goal > cur else -steps[n]))
                        remaining = True
                if not remaining:
                    return True
                if self._abort.wait(SLEW_TICK_S) or (stop_event is not None and stop_event.is_set()):
                    return False

    def go_neutral(self, deg_per_s: float = NEUTRAL_RETURN_DEG_S, only_known: bool = False,
                   stop_event: Optional[threading.Event] = None) -> bool:
        """Smoothly return servos to their configured neutral pose. only_known skips
        servos that were never commanded (used at shutdown so idle servos stay put)."""
        targets = {n: SERVO_SAFETY[n]["neutral"] for n in SERVO_CONFIG
                   if not only_known or self.state[n]["angle"] is not None}
        return self.move_many(targets, deg_per_s, stop_event) if targets else True

    def abort_motion(self) -> None:
        """Interrupt any slew in progress (servos hold position) and wait for it to exit."""
        self._abort.set()
        with self._lock:
            pass
        self._abort.clear()

    def stop_servo(self, name: str) -> None:
        pin = PINS.get(name)
        if self.pi:
            try:
                self.pi.set_servo_pulsewidth(pin, 0)
            except Exception:
                pass
        self.state[name]["pulse"] = 0

    def rgb_set_color(self, r:int, g:int, b:int, remember:bool=True) -> None:
        r, g, b = (int(max(0, min(255, v))) for v in (r, g, b))
        for color, v in [("r",r), ("g",g), ("b",b)]:
            duty = 255 - v if COMMON_ANODE else v
            pin = self.rgb_pins[color]
            if self.pi:
                try:
                    self.pi.set_PWM_dutycycle(pin, duty)
                except Exception as e:
                    print(f"[LED] write failed: {e}")
        self.rgb_state.update({"r":r, "g":g, "b":b, "on":(r!=0 or g!=0 or b!=0)})
        self.command_log.append(("rgb", r, g, b))
        if remember:
            self.rgb_state["last"] = (r, g, b)

    def rgb_off(self) -> None:
        self.rgb_set_color(0,0,0, remember=False)

    def cleanup(self) -> None:
        if self.pi:
            try:
                for s in SERVO_CONFIG.keys():
                    pin = PINS.get(s)
                    self.pi.set_servo_pulsewidth(pin, 0)
                for p in self.rgb_pins.values():
                    self.pi.set_PWM_dutycycle(p, 0)
                self.pi.stop()
            except Exception:
                pass
            self.pi = None

# ------------ Demo Mode (one worker thread, interruptible) ------------
# Choreography. Each beat has a mood (LED colour + breathing) and keyframes
# (time_s, {servo: frac}). frac runs from -1 to 1 across that servo's demo range,
# with 0 = neutral. -1 is left or up and +1 is right or down, matching do_look.
# Eyes lead and the head follows, the way people turn to look at something.
DEMO_MOODS = {
    "calm":    {"color": (0, 70, 255),    "pulse_s": 4.5, "depth": 0.40},
    "curious": {"color": (0, 200, 255),   "pulse_s": 3.0, "depth": 0.30},
    "think":   {"color": (150, 40, 255),  "pulse_s": 5.0, "depth": 0.45},
    "alert":   {"color": (255, 120, 0),   "pulse_s": 1.8, "depth": 0.25},
    "bright":  {"color": (255, 255, 255), "pulse_s": 3.5, "depth": 0.20},
}

def _beat_look_around(rng):
    s = rng.choice((-1, 1))
    return "curious", 3.2, [(0.0, {"eyes_lr": s * 1.0, "eyes_ud": rng.uniform(-0.3, 0.3)}),
                            (0.15, {"pan": s * rng.uniform(0.6, 1.0), "tilt": rng.uniform(-0.4, 0.3)}),
                            (1.1, {"eyes_lr": s * 0.25}),               # eyes re-centre once the head arrives
                            (2.4, {"eyes_lr": -s * 0.4})]               # quick counter-glance

def _beat_glance(rng):
    return "calm", 2.0, [(0.0, {"eyes_lr": rng.choice((-1, 1)) * rng.uniform(0.7, 1.0), "eyes_ud": rng.uniform(-0.5, 0.5)}),
                         (1.0, {"eyes_lr": 0.0, "eyes_ud": 0.0})]

def _beat_think(rng):
    s = rng.choice((-1, 1))
    return "think", 3.6, [(0.0, {"eyes_ud": -1.0, "eyes_lr": s * 0.6}),
                          (0.3, {"tilt": -0.8, "pan": s * 0.35}),
                          (2.6, {"eyes_ud": -0.4, "eyes_lr": s * 0.2})]

def _beat_nod(rng):
    return "alert", 2.6, [(0.0, {"eyes_lr": 0.0, "eyes_ud": 0.0, "pan": 0.0}),
                          (0.2, {"tilt": 0.8}), (0.9, {"tilt": -0.3}), (1.6, {"tilt": 0.0})]

def _beat_scan(rng):
    s = rng.choice((-1, 1))
    return "curious", 5.0, [(0.0, {"eyes_lr": -s * 0.8, "pan": -s * 0.9}),
                            (1.8, {"eyes_lr": s * 0.9}), (2.1, {"pan": s * 0.9}),
                            (3.8, {"eyes_lr": 0.0, "pan": 0.0, "tilt": 0.0})]

def _beat_rest(rng):
    # idle "breathing": everything near neutral, tiny slow tilt drift, slow LED pulse
    return rng.choice(("calm", "bright")), 3.0, [(0.0, {n: 0.0 for n in ("eyes_lr", "eyes_ud", "pan")}),
                                                 (0.0, {"tilt": 0.2}), (1.5, {"tilt": -0.1})]

DEMO_BEATS = [(_beat_look_around, 3), (_beat_glance, 3), (_beat_think, 2), (_beat_nod, 1), (_beat_scan, 2)]

class DemoController:
    """Makes SAINT look alive with choreographed beats: eyes and head glance together,
    the LED fades between mood colours and breathes, and there is an optional spoken
    intro. Every target stays inside the servo's demo_min..demo_max, the neck is
    slew-limited and the mouth is never commanded. stop() returns within about one tick."""

    def __init__(self, controller: ServoController, on_state=None, on_error=None,
                 rng: Optional[random.Random] = None, neck_enabled: Optional[bool] = None):
        self.ctrl = controller
        self.on_state = on_state
        self.on_error = on_error
        self.rng = rng or random.Random()
        self.neck_enabled = DEMO_CONFIG["neck_enabled"] if neck_enabled is None else bool(neck_enabled)
        self.state = "idle"          # idle | running | stopping | error
        self.mood = "calm"
        self.last_error: Optional[BaseException] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive())

    @property
    def servos(self):
        return ("eyes_lr", "eyes_ud") + (("pan", "tilt") if self.neck_enabled else ())

    def _set_state(self, state: str) -> None:
        self.state = state
        if self.on_state:
            try:
                self.on_state(state)
            except Exception as e:
                print("[DEMO] state callback error:", e)

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self.last_error = None
            self._thread = threading.Thread(target=self._run, name="SaintDemo", daemon=True)
            self._set_state("running")
            self._thread.start()
            return True

    def stop(self, timeout: float = 1.0) -> bool:
        """Signal the worker and wait for it. Servos hold their current (in-range) pose."""
        with self._lock:
            t = self._thread
            if not t or not t.is_alive():
                return True
            self._set_state("stopping")
            self._stop.set()
        if t is not threading.current_thread():
            t.join(timeout)
        return not t.is_alive()

    @staticmethod
    def demo_point(name: str, frac: float) -> float:
        """frac -1..1 -> angle between demo_min and demo_max, 0 = neutral."""
        s = SERVO_SAFETY[name]
        frac = max(-1.0, min(1.0, float(frac)))
        span = (s["neutral"] - s["demo_min"]) if frac < 0 else (s["demo_max"] - s["neutral"])
        return max(s["demo_min"], min(s["demo_max"], s["neutral"] + frac * span))

    def _next_beat(self, count: int, prev):
        if count == 0:
            return _beat_scan(self.rng), _beat_scan     # open with a sweep: shows off the full envelope
        if count % 4 == 0:
            return _beat_rest(self.rng), _beat_rest
        choices = [(f, w) for f, w in DEMO_BEATS if f is not prev] or DEMO_BEATS
        fn = self.rng.choices([f for f, _ in choices], weights=[w for _, w in choices])[0]
        return fn(self.rng), fn

    def _run(self) -> None:
        rs = self.ctrl.rgb_state
        prev_rgb = (rs["r"], rs["g"], rs["b"])
        try:
            speeds = {n: DEMO_CONFIG["neck_speed_deg_s" if n in ("pan", "tilt") else "eye_speed_deg_s"] for n in self.servos}
            # glide to the neutral pose first so every demo command is inside the demo range
            if not self.ctrl.move_many({n: SERVO_SAFETY[n]["neutral"] for n in self.servos}, speeds, self._stop):
                return
            intro = DEMO_CONFIG.get("intro_line")
            if intro and AGENT:
                try:
                    AGENT.tts_say(intro)
                except Exception as e:
                    print("[DEMO] intro speech failed:", e)
            self._loop(speeds)
        except Exception as e:
            self.last_error = e
            print("[DEMO ERROR]", e)
            traceback.print_exc()
            if self.on_error:
                try:
                    self.on_error(e)
                except Exception:
                    pass
        finally:
            try:
                self.ctrl.rgb_set_color(*prev_rgb, remember=False)
            except Exception as e:
                print("[DEMO] LED restore failed:", e)
            self._set_state("error" if self.last_error else "idle")

    def _loop(self, speeds) -> None:
        tick = DEMO_CONFIG["tick_s"]
        tempo = DEMO_CONFIG["tempo"]
        goals = {n: 0.0 for n in self.servos}
        count, prev_fn = 0, None
        color_from = color_now = (0, 0, 0)
        t0 = time.monotonic()
        while not self._stop.is_set():
            (mood, duration, keyframes), prev_fn = self._next_beat(count, prev_fn)
            count += 1
            self.mood = mood
            m = DEMO_MOODS[mood]
            color_from, fade_start = color_now, time.monotonic()
            beat_start = fade_start
            keyframes = sorted(keyframes, key=lambda k: k[0])
            ki = 0
            while not self._stop.is_set():
                now = time.monotonic()
                elapsed = (now - beat_start) / tempo
                while ki < len(keyframes) and keyframes[ki][0] <= elapsed:
                    goals.update({n: f for n, f in keyframes[ki][1].items() if n in goals})
                    ki += 1
                settled = True
                for n in self.servos:
                    cur = self.ctrl.state[n]["angle"]
                    goal = self.demo_point(n, goals[n])
                    step = speeds[n] * tick
                    if abs(goal - cur) > 1e-6:
                        settled = False
                        self.ctrl.set_angle(n, goal if abs(goal - cur) <= step else cur + (step if goal > cur else -step))
                # LED: ease into the mood colour, then breathe
                u = min(1.0, (now - fade_start) / max(DEMO_CONFIG["led_fade_s"] * tempo, 1e-3))
                e = u * u * (3 - 2 * u)
                bright = 1.0 - m["depth"] * (0.5 - 0.5 * math.cos(2 * math.pi * (now - t0) / m["pulse_s"]))
                color_now = tuple(round(a + (b - a) * e) for a, b in zip(color_from, m["color"]))
                self.ctrl.rgb_set_color(*(round(c * bright) for c in color_now), remember=False)
                if ki >= len(keyframes) and elapsed >= duration and (settled or elapsed >= duration + 3):
                    break
                self._stop.wait(tick)

def demo_is_running() -> bool:
    return bool(DEMO and DEMO.running)

# ------------ Mouth lip-sync ------------
class MouthSync:
    """Drives the mouth servo from speech loudness.
    - Kokoro: play_envelope(samples, rate) is called right before the audio starts; the mouth
      then follows the real RMS envelope of those samples, in time with playback.
    - Other voices (no samples): talk_start()/talk_stop() flap at a syllable rate.
    Opening is smoothed (fast attack, slower release), rate-limited and dead-banded so the
    servo neither snaps nor buzzes. Writes bypass the controller's motion lock on purpose,
    so lip-sync keeps running while the head is mid-slew. All angles stay inside the hard limits."""

    def __init__(self, controller: ServoController):
        self.ctrl = controller
        self._env = None            # (t0, envelope ndarray)
        self._talking = False
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self.level = 0.0            # smoothed 0..1 (read by the GUI)
        self._last_angle = None
        self._rng = random.Random()
        self._syl_t = 0.0
        self._syl_amp = 0.0
        self._syl_len = 0.2
        threading.Thread(target=self._run, name="SaintMouth", daemon=True).start()

    # -- inputs --
    def play_envelope(self, samples, rate) -> bool:
        """Call immediately before starting playback of `samples`. False = no envelope possible."""
        if np is None:
            self.talk_start()
            return False
        try:
            x = np.asarray(samples, dtype=np.float32)
            if x.ndim > 1:
                x = x.mean(axis=1)
            frame = max(1, int(rate * MOUTH_CONFIG["frame_s"]))
            n = len(x) // frame
            if n < 2:
                return False
            rms = np.sqrt(np.mean(np.square(x[:n * frame].reshape(n, frame)), axis=1))
            ref = float(np.percentile(rms, 95))
            if ref < 1e-4:
                return False
            gate = 0.10 * ref
            env = np.clip((rms - gate) / (ref - gate), 0.0, 1.0) ** 0.8
        except Exception as e:
            print("[MOUTH] envelope failed:", e)
            self.talk_start()
            return False
        with self._lock:
            self._env = (time.monotonic(), env)
            self._talking = False
        self._wake.set()
        return True

    def talk_start(self) -> None:
        with self._lock:
            self._talking = True
            self._env = None
        self._wake.set()

    def talk_stop(self) -> None:
        with self._lock:
            self._talking = False

    def stop(self) -> None:
        with self._lock:
            self._env = None
            self._talking = False

    def test_pattern(self, seconds: float = 2.5) -> None:
        self.talk_start()
        time.sleep(seconds)
        self.talk_stop()

    # -- driver --
    def _target(self, now: float) -> float:
        with self._lock:
            env, talking = self._env, self._talking
        if env is not None:
            t0, arr = env
            i = int((now - t0 + MOUTH_CONFIG["audio_lead_s"]) / MOUTH_CONFIG["frame_s"])
            if i >= len(arr):
                with self._lock:
                    if self._env is env:
                        self._env = None
                return 0.0
            return float(arr[max(i, 0)])
        if talking:
            if now >= self._syl_t + self._syl_len:       # next syllable
                period = 1.0 / MOUTH_CONFIG["syllable_hz"]
                self._syl_t = now
                self._syl_len = period * self._rng.uniform(0.75, 1.3)
                self._syl_amp = 0.0 if self._rng.random() < 0.08 else self._rng.uniform(0.45, 1.0)  # occasional gap
            phase = (now - self._syl_t) / self._syl_len
            return self._syl_amp * math.sin(math.pi * min(phase, 1.0))
        return 0.0

    def _write(self, angle: float) -> None:
        a = self.ctrl.clamp_angle("mouth", angle)
        self.ctrl._write("mouth", a)
        self._last_angle = a

    def _run(self) -> None:
        cfg = MOUTH_CONFIG
        closed, opened, tick = cfg["closed"], cfg["open"], cfg["tick_s"]
        last = time.monotonic()
        while True:
            try:
                if self._env is None and not self._talking and self.level < 0.01:
                    if self._last_angle is not None and abs(self._last_angle - closed) > 0.01:
                        self._write(closed)               # settle fully shut
                    self.level = 0.0
                    self._wake.wait(0.25)
                    self._wake.clear()
                    last = time.monotonic()
                    continue
                now = time.monotonic()
                dt = max(now - last, 1e-3)
                last = now
                target = self._target(now)
                tau = cfg["attack_s"] if target > self.level else cfg["release_s"]
                self.level += (target - self.level) * (1.0 - math.exp(-dt / tau))
                angle = closed + max(0.0, min(1.0, self.level)) * (opened - closed)
                if self._last_angle is None:
                    self._write(closed)
                prev = self._last_angle
                step = cfg["max_deg_s"] * dt
                angle = max(prev - step, min(prev + step, angle))
                if abs(angle - prev) >= cfg["min_step_deg"]:
                    self._write(angle)
                time.sleep(tick)
            except Exception as e:
                print("[MOUTH] error:", e)
                time.sleep(0.5)

# ------------ Ambient gaze: looks around while idle and while talking ------------
class GazeController:
    """Background look-around. Idle: eyes glance first, the head follows (usually), then both
    settle back. Talking: smaller gaze shifts. Listening/processing: eyes + head face forward.
    Yields to Demo Mode, to manual servo commands (for GAZE_CONFIG['yield_s']) and when disabled.
    Every target is inside GAZE_RANGE (itself inside each servo's hard limits)."""
    SERVOS = ("eyes_lr", "eyes_ud", "pan", "tilt")

    def __init__(self, controller: ServoController, enabled: bool = True):
        self.ctrl = controller
        self.enabled = bool(enabled)
        self.mode = "off"            # off | idle | talk | attend
        self.rng = random.Random()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._yield_until = 0.0
        self._events: list = []      # (due_monotonic, {servo: frac})
        self._next_glance = 0.0
        self._goals: Dict[str, float] = {}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="SaintGaze", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self._events.clear()
        self._goals.clear()

    def pause_for(self, seconds: Optional[float] = None) -> None:
        """Stay out of the way (manual command / e-stop)."""
        self._yield_until = time.monotonic() + (GAZE_CONFIG["yield_s"] if seconds is None else seconds)
        self._events.clear()
        self._goals.clear()

    def glance_now(self) -> bool:
        """Showcase button: start a look-around right away (needs ambient gaze enabled)."""
        if not self.enabled:
            return False
        self._yield_until = 0.0
        self._next_glance = 0.0
        return True

    @staticmethod
    def point(name: str, frac: float) -> float:
        """frac -1..1 -> angle inside GAZE_RANGE, 0 = neutral. -1 is left/up like do_look."""
        lo, hi = GAZE_RANGE[name]
        n = SERVO_SAFETY[name]["neutral"]
        frac = max(-1.0, min(1.0, float(frac)))
        return max(lo, min(hi, n + frac * ((n - lo) if frac < 0 else (hi - n))))

    def _current_mode(self, now: float) -> str:
        if not self.enabled or demo_is_running() or now < self._yield_until:
            return "off"
        if AGENT and getattr(AGENT, "is_speaking", False):
            return "talk"
        gui = CURRENT_GUI
        if gui is not None and str(getattr(gui, "_voice_status", "Idle")).rstrip(". ") in ("Listening", "Processing", "Executing"):
            return "attend"
        return "idle"

    def _plan(self, mode: str, now: float) -> None:
        r = self.rng
        if mode == "attend":
            self._events = [(now, {n: 0.0 for n in self.SERVOS})]
            self._next_glance = now + 1e9
            return
        scale = GAZE_CONFIG["talk_scale"] if mode == "talk" else 1.0
        s = r.choice((-1, 1)) * r.uniform(0.35, 1.0) * scale
        ev = [(0.0, {"eyes_lr": s, "eyes_ud": r.uniform(-0.6, 0.6) * scale})]
        if r.random() < GAZE_CONFIG["head_follow_prob"] * (0.6 if mode == "talk" else 1.0):
            ev.append((r.uniform(0.15, 0.35), {"pan": s * r.uniform(0.5, 1.0), "tilt": r.uniform(-0.6, 0.5) * scale}))
            ev.append((r.uniform(1.0, 1.8), {"eyes_lr": s * 0.25, "eyes_ud": 0.0}))   # eyes recentre as the head arrives
        else:
            ev.append((r.uniform(0.9, 1.8), {"eyes_lr": s * r.choice((0.0, -0.3)), "eyes_ud": 0.0}))
        if r.random() < 0.55:
            ev.append((r.uniform(2.2, 3.4), {"pan": 0.0, "tilt": 0.0, "eyes_lr": 0.0}))  # back to centre
        self._events = [(now + d, f) for d, f in ev]
        lo, hi = GAZE_CONFIG["talk_wait_s" if mode == "talk" else "idle_wait_s"]
        self._next_glance = now + r.uniform(lo, hi)

    def _run(self) -> None:
        tick = GAZE_CONFIG["tick_s"]
        speeds = {n: GAZE_CONFIG["neck_speed_deg_s" if n in ("pan", "tilt") else "eye_speed_deg_s"] for n in self.SERVOS}
        try:
            if any(self.ctrl.state[n]["angle"] is None for n in self.SERVOS):
                self.ctrl.move_many({n: SERVO_SAFETY[n]["neutral"] for n in self.SERVOS}, speeds, self._stop)
        except Exception as e:
            print("[GAZE] startup move failed:", e)
        prev_mode = "off"
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                mode = self._current_mode(now)
                if mode != self.mode:
                    self.mode = mode
                if mode == "off":
                    prev_mode = "off"
                    self._stop.wait(0.1)
                    continue
                if mode != prev_mode:
                    if mode == "attend" or prev_mode == "attend":
                        self._events.clear()
                        self._next_glance = now          # re-plan immediately on a mode change
                    elif prev_mode == "off":
                        self._next_glance = now + 0.5
                    prev_mode = mode
                if now >= self._next_glance:
                    self._plan(mode, now)
                due = [e for e in self._events if e[0] <= now]
                if due:
                    self._events = [e for e in self._events if e[0] > now]
                    for _, fr in due:
                        for n, f in fr.items():
                            self._goals[n] = self.point(n, f)
                for n in self.SERVOS:
                    goal = self._goals.get(n)
                    cur = self.ctrl.state[n]["angle"]
                    if goal is None or cur is None:
                        continue
                    step = speeds[n] * tick
                    if abs(goal - cur) > 1e-6:
                        self.ctrl.set_angle(n, goal if abs(goal - cur) <= step else cur + (step if goal > cur else -step))
                self._stop.wait(tick)
            except Exception as e:
                print("[GAZE] error:", e)
                self._stop.wait(0.5)

def gaze_yield(seconds: Optional[float] = None) -> None:
    """Tell ambient gaze to stay out of the way after a manual servo command."""
    if GAZE:
        GAZE.pause_for(seconds)

# ------------ Agent (TTS) ------------
def split_sentences(text: str, max_len: int = 220) -> list:
    """Split into short sentence chunks so Kokoro can start speaking sooner."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?;:])\s+|\n+", str(text)) if p.strip()]
    out = []
    for p in parts:
        while len(p) > max_len:
            cut = p.rfind(" ", 0, max_len)
            cut = cut if cut > 40 else max_len
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out

class Agent:
    """
    TTS agent: Kokoro (kokoro-onnx) first, then pyttsx3, then espeak-ng, then console.
    - Exposes: tts_say(text, block=False), tts_stop(), is_speaking, backend.
    - Speech is queued and spoken by one worker thread, sentence by sentence.
    - While speaking, is_speaking is True and the wake listener ignores the mic
      (see mic_blocked()); the user's own MUTE_MIC choice is never touched.
    - MUTE_SPEAKER silences everything, including speech already in progress.
    """
    def __init__(self, backend: Optional[str] = None):
        self.is_speaking = False
        self.kokoro = None
        self._pyttsx = None
        self._proc = None
        self._gen = 0                      # bumped by tts_stop(); stale queued speech is dropped
        self._busy = False                 # worker is handling an item
        self._queue: "queue.Queue" = queue.Queue()
        self.backend = backend or self._pick_backend()
        print(f"[TTS] backend: {self.backend}")
        self.mouth = MouthSync(CONTROLLER) if CONTROLLER else None   # lip-sync (physical robot)
        threading.Thread(target=self._worker, name="SaintTTS", daemon=True).start()

    def _pick_backend(self) -> str:
        pref = TTS_ENGINE
        if pref in ("auto", "kokoro") and Kokoro is not None and sd is not None:
            if os.path.exists(KOKORO_MODEL_PATH) and os.path.exists(KOKORO_VOICES_PATH):
                try:
                    self.kokoro = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
                    return "kokoro"
                except Exception as e:
                    print("[TTS] Kokoro load failed, falling back:", e)
            else:
                print(f"[TTS] Kokoro model files missing ({KOKORO_MODEL_PATH}); run setup_pi.sh")
        if pref in ("auto", "kokoro", "pyttsx3") and HAS_TTS:
            return "pyttsx3"
        if pref != "print" and (shutil.which("espeak-ng") or shutil.which("espeak")):
            return "espeak"
        return "print"

    def tts_say(self, text: str, block: bool = False):
        """Queue text to speak. With block=True, return once it has been spoken (or dropped)."""
        if not text:
            return
        if MUTE_SPEAKER:
            print("[TTS muted]", text)
            return
        done = threading.Event()
        self.is_speaking = True  # block the mic from the moment speech is queued
        self._queue.put((self._gen, str(text), done))
        if block:
            done.wait(timeout=120)

    def tts_stop(self):
        """Stop speech now and drop anything queued."""
        self._gen += 1
        try:
            while True:
                _, _, done = self._queue.get_nowait()
                done.set()
        except queue.Empty:
            pass
        self._interrupt_audio()
        if self.mouth:
            self.mouth.stop()
        if not self._busy:
            self.is_speaking = False

    def _interrupt_audio(self):
        try:
            if self.backend == "kokoro" and sd is not None:
                sd.stop()
            elif self.backend == "pyttsx3" and self._pyttsx:
                self._pyttsx.stop()
            elif self.backend == "espeak" and self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception as e:
            print("[TTS] stop error:", e)

    def _cancelled(self, gen: int) -> bool:
        return gen != self._gen or MUTE_SPEAKER

    def _worker(self):
        while True:
            gen, text, done = self._queue.get()
            self._busy = True
            try:
                if not self._cancelled(gen):
                    self.is_speaking = True
                    # voices without raw samples get a syllable-rate flap; Kokoro uses the real envelope
                    if self.mouth and self.backend in ("pyttsx3", "espeak"):
                        self.mouth.talk_start()
                    self._speak(gen, text)
            except Exception as e:
                print("[TTS ERROR]", e)
            finally:
                if self.mouth:
                    if self.backend == "kokoro":
                        if self._queue.empty():
                            self.mouth.stop()
                    else:
                        self.mouth.talk_stop()
                done.set()
                if self._queue.empty():
                    time.sleep(0.25)  # let the room echo die before the mic listens again
                    self.is_speaking = not self._queue.empty()
                self._busy = False

    def _speak(self, gen: int, text: str):
        if self.backend == "kokoro":
            chunks = split_sentences(text)
            nxt = self._kokoro_synth(chunks[0]) if chunks else None
            for i in range(len(chunks)):
                if self._cancelled(gen) or nxt is None:
                    return
                samples, rate = nxt
                if self.mouth:
                    self.mouth.play_envelope(samples, rate)   # mouth follows this audio from this instant
                sd.play(samples, rate)
                # synthesize the next sentence while this one plays
                nxt = self._kokoro_synth(chunks[i + 1]) if i + 1 < len(chunks) else None
                sd.wait()
        elif self.backend == "pyttsx3":
            if self._pyttsx is None:
                self._pyttsx = pyttsx3.init()  # created on the worker thread that runs it
                try:
                    self._pyttsx.setProperty("rate", int(self._pyttsx.getProperty("rate") * 0.95))
                except Exception:
                    pass
            self._pyttsx.say(text)
            self._pyttsx.runAndWait()
        elif self.backend == "espeak":
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            for chunk in split_sentences(text):
                if self._cancelled(gen):
                    return
                self._proc = subprocess.Popen([exe, "-s", "160", chunk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._proc.wait()
        else:
            print("[TTS FALLBACK] would say:", text)

    def _kokoro_synth(self, chunk: str):
        samples, rate = self.kokoro.create(chunk, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang=KOKORO_LANG)
        return samples, rate

def mic_blocked() -> bool:
    """True while the user muted the mic or SAINT is talking (so it can't hear itself)."""
    return MUTE_MIC or bool(AGENT and getattr(AGENT, "is_speaking", False))

# End Agent class

# ------------ Rhino intent recognizer wrapper (optional) ------------
class RhinoIntentRecognizer:
    def __init__(self, context_path: str, access_key: str):
        self.rhino = None
        self.context_path = context_path
        self.access_key = access_key
        if not HAS_RHINO:
            print("[RHINO] pvrhino not installed")
            return
        if not os.path.exists(context_path):
            print(f"[RHINO] context not found: {context_path}")
            return
        if not access_key:
            print("[RHINO] no access key")
            return
        try:
            self.rhino = pvrhino.create(access_key=access_key, context_path=context_path)
            print("[RHINO] initialized")
        except Exception as e:
            print("[RHINO] init error:", e)
            self.rhino = None

    def process_audio_frame(self, pcm_frame):
        if not self.rhino:
            return False
        try:
            return self.rhino.process(pcm_frame)
        except Exception as e:
            print("[RHINO] process error:", e)
            return False

    def get_inference(self):
        if not self.rhino:
            return None
        try:
            inf = self.rhino.get_inference()
            if inf.is_understood:
                return {"is_understood": True, "intent": inf.intent, "slots": inf.slots}
            return {"is_understood": False}
        except Exception as e:
            print("[RHINO] inference error", e)
            return None

    def cleanup(self):
        if self.rhino:
            try:
                self.rhino.delete()
            except Exception:
                pass

# ------------ ONNX Wake model wrapper ------------
class ONNXWakeModel:
    def __init__(self, model_path: str):
        if not ort:
            raise RuntimeError("onnxruntime not installed")
        if not os.path.exists(model_path):
            raise FileNotFoundError("Wake model not found: " + model_path)
        self.sess = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        inputs = self.sess.get_inputs()
        self.input_name = inputs[0].name
        self.input_shape = inputs[0].shape
        print(f"[WAKE] ONNX loaded: input_name={self.input_name} shape={self.input_shape}")

    def predict(self, audio_samples):
        x = np.asarray(audio_samples, dtype=np.float32)
        if len(self.input_shape) == 3:
            x = x.reshape(1, 1, -1)
        elif len(self.input_shape) == 2:
            x = x.reshape(1, -1)
        outputs = self.sess.run(None, {self.input_name: x})
        arr = np.asarray(outputs[0]).ravel()
        if arr.size == 0:
            return 0.0, arr
        score = float(np.max(arr)) if arr.size > 1 else float(arr[0])
        score = max(0.0, min(1.0, score))
        return score, arr

# ------------ Knowledge helpers: geocode, weather, time, search ------------
def geocode_location(query: str):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": query, "format": "json", "limit": 1}
        r = requests.get(url, params=params, headers=HEADERS, timeout=8)
        r.raise_for_status()
        j = r.json()
        if not j:
            return None
        itm = j[0]
        return float(itm["lat"]), float(itm["lon"]), itm.get("display_name", query)
    except Exception as e:
        print("[GEOCODE] error", e)
        return None

def get_weather_for_location(location: Optional[str]):
    if not location:
        try:
            ipres = requests.get("http://ip-api.com/json", timeout=6).json()
            if ipres and ipres.get("lat") and ipres.get("lon"):
                lat, lon = float(ipres["lat"]), float(ipres["lon"])
                display = f"{ipres.get('city','')}, {ipres.get('country','')}"
            else:
                return {"ok": False, "error": "IP lookup failed"}
        except Exception:
            return {"ok": False, "error": "IP lookup failed"}
    else:
        geo = geocode_location(location)
        if not geo:
            return {"ok": False, "error": f"Could not geocode {location}"}
        lat, lon, display = geo
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {"latitude": lat, "longitude": lon, "current_weather": True, "timezone": "UTC"}
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        j = r.json()
        cw = j.get("current_weather")
        if not cw:
            return {"ok": False, "error": "No current weather returned"}
        return {"ok": True, "location": display, "temperature_c": cw.get("temperature"), "wind_kph": cw.get("windspeed"), "raw": cw}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_time_for_location(location: Optional[str]):
    if not location:
        now = datetime.now()
        return {"ok": True, "location": "local system", "datetime": now.isoformat(), "readable": now.strftime("%Y-%m-%d %H:%M:%S")}
    geo = geocode_location(location)
    if not geo:
        return {"ok": False, "error": f"Could not geocode {location}"}
    lat, lon, display = geo
    try:
        url = "https://timeapi.io/api/Time/current/coordinate"
        params = {"latitude": lat, "longitude": lon}
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return {"ok": False, "error": "Time API error"}
        j = r.json()
        return {"ok": True, "location": display, "datetime": j.get("dateTime"), "readable": j.get("dateTime")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def wiki_summary_for_query(query: str):
    try:
        wikipedia.set_lang("en")
        search = wikipedia.search(query, results=3)
        if not search:
            return None
        page = wikipedia.page(search[0], auto_suggest=False)
        summary = wikipedia.summary(page.title, sentences=2)
        return {"title": page.title, "summary": summary, "url": page.url}
    except Exception as e:
        print("[WIKI] error", e)
        return None

def dictionary_lookup(word: str):
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
        r = requests.get(url, timeout=6)
        if r.status_code != 200:
            return None
        j = r.json()
        if isinstance(j, list) and j:
            entry = j[0]
            defs = []
            for m in entry.get("meanings", []):
                for d in m.get("definitions", []):
                    defs.append(d.get("definition"))
            return {"word": entry.get("word"), "definitions": defs[:3]}
        return None
    except Exception as e:
        print("[DICT] error", e)
        return None

def duckduckgo_instant_answer(query: str):
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}
        r = requests.get(url, params=params, headers=HEADERS, timeout=6)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("[DDG] error", e)
        return None

def answer_question(query: str) -> dict:
    q = query.strip()
    m = re.search(r"(?:how do you spell|spell)\s+['\"]?([A-Za-z\-]+)['\"]?", q, flags=re.I)
    if m:
        word = m.group(1)
        d = dictionary_lookup(word)
        if d:
            return {"ok": True, "answer": f"Spelling: {d['word']}. Definitions: {d.get('definitions', [])}"}
        return {"ok": True, "answer": f"Spelling: {word}"}

    if re.search(r"\b(tallest|highest|largest)\b.*\b(building|skyscraper|tower)\b", q, flags=re.I):
        try:
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/Burj_Khalifa"
            r = requests.get(url, headers=HEADERS, timeout=6)
            if r.status_code == 200:
                j = r.json()
                extract = j.get("extract", "")
                mht = re.search(r"([0-9]{2,4})\s*m(?:et(?:re|er)s?)?.*?\(?\s*([\d,]{3})\s*ft", extract, flags=re.I)
                if mht:
                    m_val = mht.group(1)
                    ft_val = mht.group(2)
                    ans = f"The tallest building currently is the Burj Khalifa — {m_val} m ({ft_val} ft) tall."
                    return {"ok": True, "answer": ans}
                title = j.get("title", "Burj Khalifa")
                ans = f"{title}: {extract}"
                return {"ok": True, "answer": ans}
        except Exception as e:
            print("[ANS TALLEST ERR]", e)
        wk = wiki_summary_for_query("List of tallest buildings")
        if wk:
            return {"ok": True, "answer": f"{wk['title']}: {wk['summary']} (more: {wk['url']})"}
        return {"ok": False, "error": "Couldn't locate tallest building info."}

    ddg = duckduckgo_instant_answer(q)
    if ddg:
        abstract = ddg.get("AbstractText") or ddg.get("Answer") or ddg.get("Definition")
        if abstract:
            return {"ok": True, "answer": abstract}
    wk = wiki_summary_for_query(q)
    if wk:
        return {"ok": True, "answer": f"{wk['title']}: {wk['summary']} (more: {wk['url']})"}
    return {"ok": False, "error": "No clear answer found."}

# ------------ Notifications + timer helper functions ------------
def notify_now(message: str, title: str = "SAINT") -> None:
    """
    Cross-platform notification:
    - try plyer.notification
    - else if GUI present, use messagebox via GUI thread
    - else print to console
    """
    try:
        if plyer_notification:
            try:
                plyer_notification.notify(title=title, message=message, app_name="SAINT")
                return
            except Exception as e:
                print("[PLYER NOTIFY ERR]", e)
        if CURRENT_GUI:
            try:
                # ensure runs on GUI thread
                def _show():
                    try:
                        messagebox.showinfo(title, message)
                    except Exception:
                        print("[NOTIFY MSGBOX ERR]")
                CURRENT_GUI.root.after(0, _show)
                return
            except Exception:
                pass
    except Exception:
        pass
    # fallback
    print(f"[NOTIFY] {title}: {message}")

def start_timer(seconds: int, label: Optional[str]=None) -> dict:
    """
    Start a timer: schedule a reminder and open a countdown window.
    Safe to call from background threads.
    """
    if seconds <= 0:
        return {"ok": False, "error": "seconds must be > 0"}
    message = label or "Timer"
    # schedule the reminder using existing scheduling system (so it fires same way)
    res = set_timer_from_seconds(seconds, message)
    # open GUI window safely
    try:
        if CURRENT_GUI and CURRENT_GUI.root:
            CURRENT_GUI.root.after(0, lambda: CURRENT_GUI.open_countdown_window(seconds, message))
        else:
            # spawn a short-lived tkinter loop for this countdown so timers still show even without main GUI
            def _run_timer_window():
                try:
                    wroot = tk.Tk()
                    wroot.title(f"Timer: {message}")
                    lbl = ttk.Label(wroot, text="", font=("Helvetica", 20))
                    lbl.pack(padx=12, pady=12)
                    end_time = time.time() + seconds
                    def update():
                        remaining = int(round(end_time - time.time()))
                        if remaining <= 0:
                            lbl.config(text="00:00")
                            try:
                                notify_now(f"Timer finished: {message}")
                                if AGENT:
                                    AGENT.tts_say(f"Timer finished: {message}")
                            except Exception:
                                pass
                            # close after a short pause
                            wroot.after(800, wroot.destroy)
                            return
                        mins, secs = divmod(remaining, 60)
                        lbl.config(text=f"{mins:02d}:{secs:02d}")
                        wroot.after(250, update)
                    update()
                    wroot.mainloop()
                except Exception as e:
                    print("[TIMER WINDOW ERR]", e)
            th = threading.Thread(target=_run_timer_window, daemon=True)
            th.start()
    except Exception as e:
        print("[START_TIMER ERR]", e)
    return res

def schedule_reminder_in(seconds: int, message: str) -> dict:
    dt = datetime.now() + timedelta(seconds=seconds)
    return schedule_reminder(dt, message)

# ------------ Reminders: persistence + scheduling ------------
def ensure_reminders_db_exists():
    if not os.path.exists(REMINDERS_DB):
        try:
            with open(REMINDERS_DB, "w", encoding="utf-8") as f:
                json.dump([], f)
        except Exception as e:
            print("[REM DB CREATE ERR]", e)

def persist_reminders():
    with REMINDERS_LOCK:
        try:
            data = []
            for rid, meta in REMINDERS.items():
                data.append({"id": rid, "time_iso": meta.get("time_iso"), "message": meta.get("message"), "fired": meta.get("fired", False)})
            with open(REMINDERS_DB, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print("[REM PERSIST ERR]", e)

def load_and_schedule_reminders():
    ensure_reminders_db_exists()
    try:
        with open(REMINDERS_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = []
    for item in data:
        try:
            rid = item.get("id") or str(uuid.uuid4())
            time_iso = item.get("time_iso")
            message = item.get("message")
            fired = item.get("fired", False)
            if fired:
                continue
            dt = None
            if time_iso:
                try:
                    dt = datetime.fromisoformat(time_iso)
                except Exception:
                    dt = dateparser.parse(time_iso, settings={"PREFER_DATES_FROM":"future"})
            if not dt:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            schedule_reminder(dt, message, rid=rid, persist=False)
        except Exception as e:
            print("[REM LOAD SCHED ERR]", e)

def trigger_reminder(rid: str) -> None:
    with REMINDERS_LOCK:
        meta = REMINDERS.get(rid)
        if not meta:
            return
        meta["fired"] = True
        persist_reminders()
    message = meta.get("message", "Reminder")
    readable = meta.get("time_iso", "")
    try:
        notify_now(f"Reminder: {message}")
    except Exception:
        pass
    try:
        if AGENT:
            AGENT.tts_say(f"Reminder: {message}")
    except Exception:
        pass
    if CURRENT_GUI:
        CURRENT_GUI.display_result({"reminder": message, "time": readable})
    print(f"[REMINDER] {readable}: {message}")

def schedule_reminder(dt: datetime, message: str, rid: Optional[str]=None, persist: bool=True) -> dict:
    if not rid:
        rid = str(uuid.uuid4())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    now = datetime.now(dt.tzinfo)
    delay = (dt - now).total_seconds()
    if delay <= 0:
        threading.Thread(target=lambda: AGENT.tts_say(f"Reminder: {message}"), daemon=True).start()
        try:
            notify_now(f"Reminder: {message}")
        except Exception:
            pass
        return {"ok": True, "id": rid, "scheduled": False}
    timer = threading.Timer(delay, lambda: trigger_reminder(rid))
    timer.daemon = True
    with REMINDERS_LOCK:
        REMINDERS[rid] = {"id": rid, "time_iso": dt.isoformat(), "message": message, "fired": False, "timer": timer}
        timer.start()
        if persist:
            persist_reminders()
    return {"ok": True, "id": rid, "scheduled": True, "time_iso": dt.isoformat()}

def set_timer_from_seconds(seconds: int, message: str):
    dt = datetime.now() + timedelta(seconds=seconds)
    return schedule_reminder(dt, message)

# ------------ Parsing natural language commands ------------
def canonical_servo_name(name: str) -> str:
    lname = str(name).lower()
    for key in SERVO_CONFIG:
        if key == lname or key.endswith(lname) or lname.endswith(key) or key.startswith(lname) or lname in key:
            return key
    return name

def do_look(direction: str, part: str="head", degrees: Optional[int]=None):
    """Step eyes or head from the current position. Without degrees it moves
    LOOK_STEP_DEG (it used to jump to the end stop). set_angle clamps to the hard
    limits and slews the neck, so no voice command can over-drive a servo."""
    if not CONTROLLER:
        return
    eyes = part.startswith("eye")
    if direction in ("left", "right"):
        key = "eyes_lr" if eyes else "pan"
    else:
        key = "eyes_ud" if eyes else "tilt"
    cur = CONTROLLER.state.get(key, {}).get("angle")
    cur = float(SERVO_SAFETY[key]["neutral"] if cur is None else cur)
    step = abs(degrees) if degrees is not None else LOOK_STEP_DEG["eyes" if eyes else "head"]
    delta = -step if direction in ("left", "up") else step
    CONTROLLER.set_angle(key, cur + delta)

# ------------ Natural language parsing (part you provided) ------------
def parse_command(text: str) -> Optional[dict]:
    t = text.strip()
    tl = t.lower()

    # reminders
    if re.match(r"\s*remind me\b", tl):
        try:
            results = dateparser.search.search_dates(t, settings={'PREFER_DATES_FROM':'future', 'RETURN_AS_TIMEZONE_AWARE': False})
            if results:
                matched_text, dt = results[-1]
                after = re.sub(r"(?i)^remind me", "", t).strip()
                remain = after.replace(matched_text, "").strip()
                remain = re.sub(r"(?i)^\bto\b[:\s]*", "", remain).strip()
                if not remain:
                    remain = "Reminder"
                return {"action": "set_reminder", "dt": dt, "message": remain}
            m = re.match(r"(?i)remind me in (\d+)\s*(second|seconds|minute|minutes|hour|hours|day|days)\s*(?:to)?\s*(.*)", t)
            if m:
                val = int(m.group(1)); unit = m.group(2).lower(); rest = m.group(3).strip() or "Reminder"
                secs = val * (60 if 'minute' in unit else 3600 if 'hour' in unit else 86400 if 'day' in unit else 1)
                dt = datetime.now() + timedelta(seconds=secs)
                return {"action": "set_reminder", "dt": dt, "message": rest}
        except Exception:
            pass
        return {"action": "set_reminder", "dt": datetime.now(), "message": re.sub(r"(?i)^remind me", "", t).strip() or "Reminder"}

    # timer
    m = re.search(r"(?i)(?:set (?:a )?timer for|timer for|set timer for)\s*(\d+)\s*(seconds|second|minutes|minute|hours|hour)\b", t)
    if m:
        val = int(m.group(1)); unit = m.group(2).lower()
        secs = val * (60 if 'minute' in unit else 3600 if 'hour' in unit else 1)
        return {"action": "set_timer", "seconds": secs, "message": "Timer"}
    m2 = re.search(r"(?i)\bin\s+(\d+)\s*(seconds|second|minutes|minute|hours|hour)\b(?:.*timer)?(?: to)?\s*(.*)", t)
    if m2 and ("timer" in tl or "remind" not in tl):
        val = int(m2.group(1)); unit = m2.group(2).lower(); rest = m2.group(3).strip() or "Timer"
        secs = val * (60 if 'minute' in unit else 3600 if 'hour' in unit else 1)
        return {"action": "set_timer", "seconds": secs, "message": rest}

    # weather
    m = re.search(r"(?i)\bweather\b(?:\s*(?:in|at)\s*(.+))?", t)
    if m:
        loc = m.group(1).strip() if m.group(1) else ""
        return {"action": "get_weather", "location": loc or ""}

    # time
    m = re.search(r"(?i)\bwhat(?:'s| is)?\s+the time(?: in\s+(.+))?|\bwhat time is it(?: in\s+(.+))?", t)
    if m:
        loc = (m.group(1) or m.group(2) or "").strip()
        return {"action": "get_time", "location": loc}

    # tts speak
    # "tell me ..." is conversation (jokes, stories), not "repeat this text"
    m = re.match(r"(say|speak)\s+(.+)", t, flags=re.I)
    if m:
        return {"action": "tts", "text": m.group(2)}

    # factual queries
    if not is_small_talk(t) and re.search(r"\bhow do you spell\b|\bhow tall\b|\bwhere is\b|\bwhat is\b|\bwhat's\b|\bhow many\b|\bwho is\b|\bhow far\b", tl):
        return {"action": "answer", "query": t}

    # LED/servo commands (simple)
    m = re.match(r"(?:set|change|make|turn)\s*(?:my\s*)?(?:eye|led)s?\s*(?:to|color)?\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})", tl)
    if m:
        return {"action": "led", "r": int(m.group(1)), "g": int(m.group(2)), "b": int(m.group(3))}
    m = re.match(r"(?:set|change|make|turn)\s*(?:my\s*)?(?:eye|led)s?\s*(?:to|color)?\s*(blue|red|green|yellow|purple|cyan|white|off|black|orange|pink)", tl)
    if m:
        colors = {"blue":(0,0,255),"red":(255,0,0),"green":(0,255,0),"white":(255,255,255),"off":(0,0,0),"black":(0,0,0),"yellow":(255,255,0),"purple":(128,0,128),"cyan":(0,255,255),"orange":(255,165,0),"pink":(255,192,203)}
        rgb = colors.get(m.group(1), (255,255,255))
        return {"action": "led", "r": rgb[0], "g": rgb[1], "b": rgb[2]}
    m = re.match(r"(?:look|turn|move|glance)\s+(left|right|up|down)(?:\s+(eyes?|head))?(?:\s+(\d{1,3}))?", tl)
    if m:
        direction = m.group(1); part = m.group(2) or "head"; degrees = int(m.group(3)) if m.group(3) else None
        return {"action": "look", "direction": direction, "part": part, "degrees": degrees}
    m = re.match(r"(?:move|set|put)\s+(\w+)\s+(?:to|servo|at)?\s*(\d{1,3})", tl)
    if m:
        return {"action": "servo_move", "servo": m.group(1), "angle": int(m.group(2))}
    if re.search(r"(?:center|reset|home|neutral)\b.*(?:head|all|servos?|position)", tl):
        return {"action": "center"}

    # Spotify placeholders
    if "spotify" in tl:
        if re.search(r"\bplay\b.*on spotify", tl):
            m = re.match(r"play\s+(.+?)\s+on\s+spotify", t, flags=re.I)
            if m:
                return {"action": "spotify_play", "query": m.group(1)}
        if re.search(r"\bpause\b|\bstop\b", tl):
            return {"action": "spotify_pause"}
        if re.search(r"\bresume\b|\bplay\b", tl):
            return {"action": "spotify_resume"}
        if re.search(r"\bnext\b|\bskip\b", tl):
            return {"action": "spotify_next"}
        if re.search(r"\bprevious\b|\bprev\b|\bback\b", tl):
            return {"action": "spotify_prev"}
        if re.search(r"\bwhat'?s\b.*\bplaying\b|\bcurrently\b.*playing", tl):
            return {"action": "spotify_now"}

    return None

# ------------ Process and execute commands (with conversational fallback) ------------
def process_command(text: str) -> dict:
    parsed = parse_command(text)
    action_executed = "none"
    action_result = "no action taken"
    action_success = False
    assistant_text = None

    try:
        if parsed:
            action = parsed.get("action")
            action_executed = action
            print(f"[ACTION] {action} from: {text}")

            if action in ("led", "look", "servo_move", "center") and demo_is_running():
                # Demo Mode owns the servos/LED; don't fight it
                action_result = "Demo Mode is running. Stop the demo first."

            elif action == "tts":
                assistant_text = parsed.get("text","")
                AGENT.tts_say(assistant_text)
                action_result = f"Said: {assistant_text}"
                action_success = True

            elif action == "led":
                r,g,b = parsed.get("r",0), parsed.get("g",0), parsed.get("b",0)
                if CONTROLLER:
                    CONTROLLER.rgb_set_color(r,g,b)
                    action_result = f"Set eyes to RGB({r},{g},{b})"
                else:
                    action_result = "No controller"
                action_success = True

            elif action == "look":
                gaze_yield()
                do_look(parsed.get("direction"), parsed.get("part","head"), parsed.get("degrees"))
                action_result = f"Looked {parsed.get('direction')}"
                action_success = True

            elif action == "servo_move":
                sname = canonical_servo_name(parsed.get("servo"))
                ang = parsed.get("angle")
                if CONTROLLER:
                    gaze_yield()
                    CONTROLLER.set_angle(sname, ang)
                    actual = CONTROLLER.state[sname]["angle"]
                    action_result = f"Moved {sname} to {actual:g}°" + ("" if actual == ang else f" (limited from {ang}°)")
                else:
                    action_result = "No controller"
                action_success = True

            elif action == "center":
                if CONTROLLER:
                    gaze_yield()
                    CONTROLLER.go_neutral()
                    action_result = "Centered all servos"
                else:
                    action_result = "No controller"
                action_success = True

            elif action == "get_weather":
                loc = parsed.get("location","")
                res = get_weather_for_location(loc)
                if res.get("ok"):
                    action_result = f"Weather at {res['location']}: {res['temperature_c']}°C, wind {res['wind_kph']} kph."
                    assistant_text = action_result
                    action_success = True
                else:
                    action_result = "Weather lookup failed: " + str(res.get("error"))

            elif action == "get_time":
                loc = parsed.get("location","")
                res = get_time_for_location(loc)
                if res.get("ok"):
                    action_result = f"Time at {res['location']}: {res['readable']}"
                    assistant_text = action_result
                    action_success = True
                else:
                    action_result = "Time lookup failed: " + str(res.get("error"))

            elif action == "answer":
                q = parsed.get("query", text)
                res = answer_question(q)
                if res.get("ok"):
                    assistant_text = res.get("answer")
                    action_result = f"Answer: {assistant_text}"
                    action_success = True
                else:
                    action_result = "Answer lookup failed: " + str(res.get("error"))
                    assistant_text = generate_chat(q)  # let the local LLM have a go
                    if assistant_text:
                        action_success = True

            elif action == "set_timer":
                secs = int(parsed.get("seconds",0))
                msg = parsed.get("message","Timer")
                # use new start_timer helper so GUI window opens properly
                res = start_timer(secs, msg)
                if res.get("ok"):
                    action_result = f"Timer set for {secs} seconds (id {res.get('id')})"
                    action_success = True
                    assistant_text = f"Timer set for {secs} seconds."
                else:
                    action_result = "Failed to set timer"

            elif action == "set_reminder":
                dt = parsed.get("dt")
                msg = parsed.get("message","Reminder")
                if isinstance(dt, str):
                    dtp = dateparser.parse(dt, settings={'PREFER_DATES_FROM':'future'})
                else:
                    dtp = dt
                if not dtp:
                    action_result = "Could not parse reminder time."
                else:
                    res = schedule_reminder(dtp, msg)
                    if res.get("ok"):
                        action_result = f"Reminder scheduled: {msg} at {res.get('time_iso')}"
                        action_success = True
                        assistant_text = f"Reminder scheduled: {msg}."
                    else:
                        action_result = "Failed to schedule reminder: " + str(res)

            elif action and action.startswith("spotify_"):
                if not SPOT or not SPOT.access_token:
                    action_result = "Spotify not configured / not connected."
                else:
                    if action == "spotify_play":
                        cq = parsed.get("query","")
                        res = SPOT.play(cq)
                        if res.get("ok"):
                            action_result = f"Playing {cq}"
                            action_success = True
                        else:
                            action_result = f"Failed to play: {res}"
                    elif action == "spotify_pause":
                        res = SPOT.pause()
                        action_result = "Paused" if res.get("ok") else "Failed"
                        action_success = res.get("ok", False)
                    elif action == "spotify_resume":
                        res = SPOT.resume()
                        action_result = "Resumed" if res.get("ok") else "Failed"
                        action_success = res.get("ok", False)
                    elif action == "spotify_next":
                        res = SPOT.next_track()
                        action_result = "Skipped" if res.get("ok") else "Failed"
                        action_success = res.get("ok", False)
                    elif action == "spotify_prev":
                        res = SPOT.previous_track()
                        action_result = "Previous" if res.get("ok") else "Failed"
                        action_success = res.get("ok", False)
                    elif action == "spotify_now":
                        res = SPOT.api_request("GET", "/me/player/currently-playing")
                        if res.get("ok") and res.get("data"):
                            item = res["data"].get("item", {})
                            track = item.get("name","Unknown")
                            artists = item.get("artists", [])
                            artist = artists[0].get("name","Unknown") if artists else "Unknown"
                            action_result = f"Currently playing: '{track}' by {artist}"
                            action_success = True
                        else:
                            action_result = "Nothing playing"
        else:
            # conversational fallback — be concise, blunt but friendly (SAINT persona)
            short_replies = [
                "Not much. I'm running — anything you need?",
                "All systems nominal. What do you want me to do?",
                "I'm here. Say a command or ask a question.",
                "Status: awake. Tell me what you want."
            ]
            # aim to respond specifically for "what's up" style queries
            tl = text.strip().lower()
            if re.search(r"\bwhat(?:'s| is)? up\b|\bwhat(?:'s| is)? going on\b|\bwhats up\b", tl):
                assistant_text = "Not much — running diagnostics and waiting on your command."
            else:
                assistant_text = chat_reply(text) or random.choice(short_replies)
            action_result = "conversational reply"
            action_success = True
            action_executed = "converse"

    except Exception as e:
        print("[PROCESS ERROR]", e)
        action_result = f"Error executing action: {e}"

    # store messages in history
    save_history_entry("user", text)
    if assistant_text:
        save_history_entry("assistant", assistant_text)
        try:
            AGENT.tts_say(assistant_text)
        except Exception:
            pass
        # also provide a visual notification for reminders/timers etc when appropriate
        try:
            if action_executed in ("set_timer","set_reminder"):
                notify_now(assistant_text)
        except Exception:
            pass
        result = {"user_input": text, "action": action_executed, "action_result": action_result, "action_success": action_success, "assistant_text": assistant_text}
    else:
        ack = action_result if action_success else ("I couldn't do that. " + action_result)
        save_history_entry("assistant", ack)
        try:
            AGENT.tts_say(ack)
        except Exception:
            pass
        result = {"user_input": text, "action": action_executed, "action_result": action_result, "action_success": action_success, "assistant": ack}

    return result

# ------------ Wake word listener (uses ONNX model if present, otherwise SR fallback) ------------
class WakeWordListener(threading.Thread):
    def __init__(self, gui):
        super().__init__(daemon=True)
        self.gui = gui
        self.running = True
        self.model = None
        self.buffer = deque()
        self.lock = threading.Lock()
        self.last_trigger = 0.0
        self.sample_rate = WAKE_SAMPLE_RATE
        self.window_len = int(self.sample_rate * (WAKE_WINDOW_MS/1000.0))
        self.check_interval = 0.15
        self.stream = None
        self.recognizer = sr.Recognizer() if HAS_SR else None

        try:
            if ort and np is not None and os.path.exists(WAKE_MODEL_PATH):
                self.model = ONNXWakeModel(WAKE_MODEL_PATH)
                self.buffer = deque(maxlen=self.window_len*2)
            else:
                self.model = None
        except Exception as e:
            print("[WAKE LOAD ERROR]", e)
            self.model = None

    def audio_callback(self, indata, frames, time_info, status):
        if mic_blocked():
            return
        if not self.running:
            return
        # Extra safety: don't buffer while SAINT is actively speaking
        try:
            if AGENT and getattr(AGENT, "is_speaking", False):
                return
        except Exception:
            pass
        try:
            # keep mono float32 samples in buffer
            arr = np.copy(indata[:, 0])
            with self.lock:
                self.buffer.extend(arr.tolist())
        except Exception:
            pass


    def run(self):
        if self.model is None or sd is None:
            if HAS_SR:
                self._fallback_loop()
            else:
                print("[WAKE] No wake model and no SR; not listening.")
                return
        else:
            try:
                self.stream = sd.InputStream(
                    channels=1,
                    samplerate=self.sample_rate,
                    callback=self.audio_callback,
                    blocksize=int(self.sample_rate * 0.02)
                )
                self.stream.start()
            except Exception as e:
                print("[WAKE] audio stream error:", e)
                self._fallback_loop()
                return

            print("[WAKE] Listening for wake word...")
            while self.running:
                time.sleep(self.check_interval)
                if mic_blocked():
                    with self.lock:
                        self.buffer.clear()
                    continue
                with self.lock:
                    if len(self.buffer) < self.window_len:
                        continue
                    audio_window = np.array(list(self.buffer)[-self.window_len:], dtype=np.float32)
                try:
                    score, _ = self.model.predict(audio_window)
                except Exception as e:
                    print("[WAKE PRED ERROR]", e)
                    continue
                now = time.time()
                if score >= WAKE_THRESHOLD and (now - self.last_trigger) > WAKE_COOLDOWN:
                    self.last_trigger = now
                    print(f"[WAKE] detected (score {score:.3f})")
                    if AGENT and AGENT.is_speaking:
                        AGENT.tts_stop()
                    self._capture_and_process()
            if self.stream:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass

    def _capture_and_process(self):
        """
        Enhanced _capture_and_process:
        - If sounddevice + numpy available: record WAKE_TEST_CAPTURE_SECONDS and transcribe.
        - Otherwise: fallback to speech_recognition.Microphone path (original behavior).
        - Handles AGENT.is_speaking and MUTE_MIC safely to avoid wake-loop during TTS.
        """
        global AGENT
        if MUTE_MIC:
            print("[WAKE] mic muted; skipping capture")
            return

        # If agent is speaking, skip capturing to avoid capturing the assistant's own voice
        try:
            if AGENT and getattr(AGENT, "is_speaking", False):
                print("[WAKE] agent is speaking; skipping capture")
                return
        except Exception:
            pass

        captured_text = None

        # Try to use sounddevice recording first (non-blocking listener may already be running)
        if sd is not None and np is not None:
            try:
                rec_seconds = float(WAKE_TEST_CAPTURE_SECONDS) if 'WAKE_TEST_CAPTURE_SECONDS' in globals() else 3.0
                print(f"[WAKE] recording {rec_seconds:.2f}s (sounddevice) for capture...")
                # Record audio (mono float32)
                recorded = sd.rec(int(rec_seconds * self.sample_rate), samplerate=self.sample_rate, channels=1, dtype='float32')
                sd.wait()
                audio_np = np.asarray(recorded).reshape(-1)
                # If SR available, convert and transcribe
                if HAS_SR and self.recognizer:
                    try:
                        int16 = np.int16(np.clip(audio_np * 32767, -32768, 32767))
                        raw_bytes = int16.tobytes()
                        audio_data = sr.AudioData(raw_bytes, self.sample_rate, 2)
                        try:
                            self.gui.set_status("Processing...")
                        except Exception:
                            pass
                        try:
                            captured_text = self.recognizer.recognize_google(audio_data)
                            print("[WAKE] recognized (sd):", captured_text)
                        except Exception as e:
                            print("[WAKE] recognition error (sd):", e)
                    except Exception as e:
                        print("[WAKE] audio->sr conversion error (sd):", e)
                else:
                    print("[WAKE] speech_recognition not available to transcribe sd capture.")
            except Exception as e:
                print("[WAKE] sounddevice record error, falling back to mic:", e)
                captured_text = None

        # If we didn't get text from sounddevice, fall back to speech_recognition.Microphone
        if not captured_text and HAS_SR and self.recognizer:
            try:
                with sr.Microphone(sample_rate=self.sample_rate) as source:
                    try:
                        self.gui.set_status("Listening...")
                    except Exception:
                        pass
                    # small ambient adjust to reduce false triggers
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.25)
                    audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=6)
                    try:
                        self.gui.set_status("Processing...")
                    except Exception:
                        pass
                    try:
                        captured_text = self.recognizer.recognize_google(audio)
                        print("[SR] recognized:", captured_text)
                    except Exception as e:
                        print("[SR] recog error:", e)
            except Exception as e:
                print("[SR] mic error:", e)

        if MUTE_MIC:
            captured_text = None
            self.gui.set_status("Idle")
            return
        # Handle result
        if captured_text:
            try:
                response = process_command(captured_text)
                try:
                    self.gui.display_result(response)
                except Exception:
                    pass
            except Exception as e:
                print("[PROCESS_CMD] error:", e)
        else:
            try:
                self.gui.display_result({"assistant": "I didn't catch that."})
            except Exception:
                pass
            try:
                if AGENT:
                    AGENT.tts_say("I didn't catch that.")
            except Exception:
                pass

        try:
            self.gui.set_status("Idle")
        except Exception:
            pass


    def _fallback_loop(self):
        if not HAS_SR:
            return
        print("[WAKE] running continuous SR fallback")
        while self.running:
            if mic_blocked():
                time.sleep(0.2)
                continue
            try:
                with sr.Microphone() as source:
                    self.gui.set_status("Listening...")
                    self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                    audio = self.recognizer.listen(source, timeout=6, phrase_time_limit=8)
                    self.gui.set_status("Processing...")
                    try:
                        text = self.recognizer.recognize_google(audio)
                        if text and not MUTE_MIC:
                            response = process_command(text)
                            self.gui.display_result(response)
                    except Exception:
                        pass
                    self.gui.set_status("Idle")
            except Exception:
                time.sleep(0.2)

# ------------ GUI (Tkinter) ------------
class SaintGUI:
    HEADER_COLORS = {  # status word -> (background, foreground)
        "Idle": ("#3a3f44", "white"), "Listening": ("#1e8e3e", "white"), "Processing": ("#1a73e8", "white"),
        "Executing": ("#1a73e8", "white"), "Demo running": ("#7b1fa2", "white"), "Stopping": ("#e37400", "white"),
        "Returning to neutral": ("#e37400", "white"), "Error": ("#c5221f", "white"),
    }
    LED_PRESETS = [("Cyan", (0,200,255)), ("Blue", (0,70,255)), ("Purple", (150,40,255)), ("Orange", (255,120,0)),
                   ("White", (255,255,255)), ("Red", (255,0,0)), ("Green", (0,255,0))]

    def __init__(self, root: tk.Tk):
        global CURRENT_GUI, DEMO
        CURRENT_GUI = self
        self.root = root
        root.title("SAINT - ONNX Wake (Enhanced)" + ("  [SIMULATION]" if CONTROLLER and CONTROLLER.pi is None else ""))
        w = min(1000, root.winfo_screenwidth())
        h = min(900, max(400, root.winfo_screenheight() - 60))
        root.geometry(f"{w}x{h}")
        self._voice_status = "Idle"
        self._demo_state = "idle"
        self._demo_error = None
        self._closing = False
        self._ui_queue = queue.SimpleQueue()  # UI updates from worker threads, drained on the Tk thread
        if DEMO is None and CONTROLLER:
            DEMO = DemoController(CONTROLLER)
        if DEMO:
            DEMO.on_state = lambda s: self._ui(lambda: self._on_demo_state(s))
            DEMO.on_error = lambda e: self._ui(lambda: self._on_demo_error(e))

        self.header = tk.Label(root, text="Idle", font=("Helvetica", 22, "bold"), pady=8)
        self.header.pack(side=tk.TOP, fill=tk.X)
        self.status_bar = ttk.Label(root, text="Idle", anchor=tk.W)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        top = ttk.Frame(root, padding=6)
        top.pack(side=tk.TOP, fill=tk.X)
        self.status_label = ttk.Label(top, text="Initializing...")
        self.status_label.pack(side=tk.LEFT, padx=4)

        ttk.Button(top, text="SAINT Login", command=self.login_prompt).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Spotify Login", command=self.spotify_login).pack(side=tk.LEFT, padx=4)

        # one tab per area of focus; header, status line and tab strip stay put (small Pi screens: tabs scroll)
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        self._tab_canvases: Dict[str, tk.Canvas] = {}
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_all(seq, self._on_mousewheel, add="+")
        tab_show = self._make_tab("  Showcase  ")
        tab_chat = self._make_tab("  Voice & Chat  ", scroll=False)
        tab_hw = self._make_tab("  Hardware  ")
        tab_tools = self._make_tab("  Tools  ")
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._refresh_live())

        demo_frame = ttk.LabelFrame(tab_show, text="Demo Mode   (Space = stop demo, Esc = emergency stop + neutral)", padding=6)
        demo_frame.pack(fill=tk.X, padx=6, pady=6)
        big = ("Helvetica", 14, "bold")
        self.start_demo_btn = tk.Button(demo_frame, text="▶  Start Demo", font=big, bg="#1e8e3e", fg="white",
                                        activebackground="#167232", width=16, command=self.start_demo)
        self.start_demo_btn.pack(side=tk.LEFT, padx=6, pady=4)
        self.stop_demo_btn = tk.Button(demo_frame, text="■  Stop Demo", font=big, bg="#c5221f", fg="white",
                                       activebackground="#a50e0e", width=16, command=self.stop_demo)
        self.stop_demo_btn.pack(side=tk.LEFT, padx=6, pady=4)
        tk.Button(demo_frame, text="E-Stop / Neutral (Esc)", font=("Helvetica", 11, "bold"),
                  command=self.emergency_stop).pack(side=tk.LEFT, padx=6, pady=4)
        neck = "eyes + head + lights" if (DEMO and DEMO.neck_enabled) else "eyes + lights (head off: --no-demo-neck)"
        ttk.Label(demo_frame, text=f"Demo: {neck}").pack(side=tk.LEFT, padx=8)

        self._build_showcase(tab_show)

        cmd_frame = ttk.LabelFrame(tab_chat, text="Manual Command", padding=6)
        cmd_frame.pack(fill=tk.X, padx=6, pady=6)
        self.cmd_text = tk.Text(cmd_frame, height=2)
        self.cmd_text.pack(fill=tk.X, padx=4, pady=4)
        self.cmd_text.bind("<Return>", lambda e: (self.execute_command(), "break")[1])
        ttk.Button(cmd_frame, text="Execute", command=self.execute_command).pack(pady=4)

        ctrl_frame = ttk.LabelFrame(tab_chat, text="Controls", padding=6)
        ctrl_frame.pack(fill=tk.X, padx=6, pady=6)
        self.mute_mic_var = tk.BooleanVar(value=False)
        self.mute_speaker_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctrl_frame, text="Mute Microphone", variable=self.mute_mic_var, command=self._on_mute_mic).grid(row=0, column=0, sticky=tk.W, padx=4)
        ttk.Checkbutton(ctrl_frame, text="Mute Speaker", variable=self.mute_speaker_var, command=self._on_mute_speaker).grid(row=0, column=1, sticky=tk.W, padx=4)
        ttk.Button(ctrl_frame, text="Show History", command=self.show_history).grid(row=0, column=2, padx=8)

        self._motion_widgets = []  # disabled while Demo Mode runs
        servo_frame = ttk.LabelFrame(tab_hw, text="Servo Control (limited to each servo's safe range)", padding=6)
        servo_frame.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(servo_frame, text="Servo:").grid(row=0, column=0, sticky=tk.W)
        self.servo_var = tk.StringVar(value="pan")
        servo_box = ttk.Combobox(servo_frame, textvariable=self.servo_var, values=list(SERVO_CONFIG.keys()), state="readonly", width=12)
        servo_box.grid(row=0, column=1)
        servo_box.bind("<<ComboboxSelected>>", lambda e: self._on_servo_selected())
        ttk.Label(servo_frame, text="Angle:").grid(row=0, column=2)
        self.angle_var = tk.IntVar(value=90)
        self.angle_scale = ttk.Scale(servo_frame, from_=0, to=180, variable=self.angle_var, orient="horizontal", length=300,
                                     command=lambda v: self.angle_var.set(int(float(v))))
        self.angle_scale.grid(row=0, column=3)
        angle_entry = ttk.Entry(servo_frame, textvariable=self.angle_var, width=6)
        angle_entry.grid(row=0, column=4)
        self.range_label = ttk.Label(servo_frame, text="")
        self.range_label.grid(row=0, column=5, padx=6)
        set_btn = ttk.Button(servo_frame, text="Set", command=self.set_servo)
        set_btn.grid(row=1, column=0, pady=4)
        center_btn = ttk.Button(servo_frame, text="Center All", command=self.center_all)
        center_btn.grid(row=1, column=1, pady=4)
        self._motion_widgets += [servo_box, self.angle_scale, angle_entry, set_btn, center_btn]
        readout = ttk.Frame(servo_frame)
        readout.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(6, 0))
        for col, title in enumerate(("Servo", "Angle", "Safe range", "Demo range", "Neutral")):
            ttk.Label(readout, text=title, font=("Helvetica", 9, "bold")).grid(row=0, column=col, sticky=tk.W, padx=8)
        self.angle_labels = {}
        for row, name in enumerate(SERVO_CONFIG, start=1):
            lo, hi = CONTROLLER.hard_range(name) if CONTROLLER else (0, 180)
            s = SERVO_SAFETY[name]
            ttk.Label(readout, text=name).grid(row=row, column=0, sticky=tk.W, padx=8)
            self.angle_labels[name] = ttk.Label(readout, text="--", font=("Courier", 10, "bold"), width=8)
            self.angle_labels[name].grid(row=row, column=1, sticky=tk.W, padx=8)
            ttk.Label(readout, text=f"{lo:g}–{hi:g}°").grid(row=row, column=2, sticky=tk.W, padx=8)
            ttk.Label(readout, text=f"{s['demo_min']:g}–{s['demo_max']:g}°").grid(row=row, column=3, sticky=tk.W, padx=8)
            ttk.Label(readout, text=f"{s['neutral']:g}°").grid(row=row, column=4, sticky=tk.W, padx=8)
        self._on_servo_selected()

        led_frame = ttk.LabelFrame(tab_hw, text="Eye LED Control", padding=6)
        led_frame.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(led_frame, text="R:").grid(row=0, column=0)
        self.r_var = tk.IntVar(value=0); r_s = ttk.Scale(led_frame, from_=0, to=255, variable=self.r_var, orient="horizontal", length=200, command=lambda v: self._update_swatch()); r_s.grid(row=0, column=1)
        ttk.Label(led_frame, text="G:").grid(row=0, column=2)
        self.g_var = tk.IntVar(value=0); g_s = ttk.Scale(led_frame, from_=0, to=255, variable=self.g_var, orient="horizontal", length=200, command=lambda v: self._update_swatch()); g_s.grid(row=0, column=3)
        ttk.Label(led_frame, text="B:").grid(row=0, column=4)
        self.b_var = tk.IntVar(value=0); b_s = ttk.Scale(led_frame, from_=0, to=255, variable=self.b_var, orient="horizontal", length=200, command=lambda v: self._update_swatch()); b_s.grid(row=0, column=5)
        self.swatch = tk.Label(led_frame, text="", width=6, relief=tk.SUNKEN, bg="#000000")
        self.swatch.grid(row=0, column=6, rowspan=2, padx=8, sticky="ns")
        set_led_btn = ttk.Button(led_frame, text="Set Color", command=self.set_led)
        set_led_btn.grid(row=1, column=0)
        off_btn = ttk.Button(led_frame, text="Off", command=self.led_off)
        off_btn.grid(row=1, column=1, sticky=tk.W)
        presets = ttk.Frame(led_frame)
        presets.grid(row=1, column=2, columnspan=4, sticky=tk.W, pady=4)
        self._motion_widgets += [r_s, g_s, b_s, set_led_btn, off_btn]
        for label, rgb in self.LED_PRESETS:
            btn = tk.Button(presets, text=label, width=7, bg="#%02x%02x%02x" % rgb,
                            fg="black" if sum(rgb) > 300 else "white", command=lambda c=rgb: self.set_led(c))
            btn.pack(side=tk.LEFT, padx=2)
            self._motion_widgets.append(btn)

        self._build_hardware_extras(tab_hw)
        self._build_tools(tab_tools)

        log_frame = ttk.LabelFrame(tab_chat, text="Conversation", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=16, wrap=tk.WORD, font=("Helvetica", 12),
                                                  padx=6, pady=6, cursor="arrow")
        self.log_text.pack(fill=tk.BOTH, expand=True)
        # chat bubbles: you on the right (blue), SAINT on the left (purple), system notes small and grey
        self.log_text.tag_configure("you", background="#d2e3fc", foreground="#0b2a5b", justify=tk.RIGHT,
                                    lmargin1=160, lmargin2=160, rmargin=6, spacing3=8)
        self.log_text.tag_configure("saint", background="#efe3fa", foreground="#3b0a57",
                                    lmargin1=6, lmargin2=6, rmargin=160, spacing3=8)
        self.log_text.tag_configure("meta_you", foreground="#5f6368", font=("Helvetica", 9), justify=tk.RIGHT, rmargin=6, spacing1=4)
        self.log_text.tag_configure("meta_saint", foreground="#5f6368", font=("Helvetica", 9), lmargin1=6, spacing1=4)
        self.log_text.tag_configure("info", foreground="#5f6368", font=("Helvetica", 10, "italic"), lmargin1=6, spacing3=4)
        self.log_text.tag_configure("error", foreground="#c5221f", font=("Helvetica", 11, "bold"), lmargin1=6, spacing3=4)
        self.log_text.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() == "c") else "break")  # read-only, copy allowed

        root.bind_all("<space>", self._on_space)
        root.bind_all("<Escape>", lambda e: self.emergency_stop())
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.wake_listener = WakeWordListener(self)
        self.wake_listener.start()

        load_history()
        load_and_schedule_reminders()

        self._refresh_header()
        self._update_status()
        self._update_readout()
        self._drain_ui_queue()
        self.display_result({"status": "GUI initialized" + (" (simulation: no hardware commands)" if CONTROLLER and CONTROLLER.pi is None else "")})

    # -- tabs --
    def _make_tab(self, title: str, scroll: bool = True) -> ttk.Frame:
        """Add a notebook tab and return the frame to build into. Scrolling tabs get a
        canvas + scrollbar (small Pi screens); the chat tab fills the space instead."""
        holder = ttk.Frame(self.notebook)
        self.notebook.add(holder, text=title)
        if not scroll:
            return holder
        canvas = tk.Canvas(holder, highlightthickness=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self._tab_canvases[str(holder)] = canvas
        return inner

    def _build_showcase(self, tab: ttk.Frame) -> None:
        live = ttk.LabelFrame(tab, text="Live robot", padding=6)
        live.pack(fill=tk.X, padx=6, pady=6)
        self.live_mode = ttk.Label(live, text="", font=("Helvetica", 11, "bold"))
        self.live_mode.grid(row=0, column=0, columnspan=4, sticky=tk.W)
        ttk.Label(live, text="Mouth").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        self.mouth_bar = ttk.Progressbar(live, orient="horizontal", length=220, maximum=100)
        self.mouth_bar.grid(row=1, column=1, columnspan=3, sticky=tk.W, padx=6, pady=(6, 0))
        self.live_angles = ttk.Label(live, text="", font=("Courier", 10))
        self.live_angles.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))

        act = ttk.LabelFrame(tab, text="Show off", padding=6)
        act.pack(fill=tk.X, padx=6, pady=6)
        self.gaze_var = tk.BooleanVar(value=bool(GAZE and GAZE.enabled))
        ttk.Checkbutton(act, text="Ambient look-around (idle + while talking)", variable=self.gaze_var,
                        command=self._on_gaze_toggle).grid(row=0, column=0, columnspan=4, sticky=tk.W)
        for col, (label, fn) in enumerate((("👀  Look around", self.look_around_now),
                                           ("👋  Say hello", self.say_hello),
                                           ("🎤  Introduce SAINT", self.introduce),
                                           ("👄  Mouth test", self.mouth_test))):
            tk.Button(act, text=label, font=("Helvetica", 11, "bold"), width=17, command=fn).grid(row=1, column=col, padx=4, pady=6)

        about = ttk.LabelFrame(tab, text="What SAINT can do", padding=6)
        about.pack(fill=tk.X, padx=6, pady=6)
        ttk.Label(about, justify=tk.LEFT, wraplength=900, text=(
            "• Wake word + speech: say the wake word, then talk. Everything runs on the Pi.\n"
            "• Moves like a robot: eyes, head and a lip-synced mouth, with colour-changing eyes.\n"
            "• Tools: timers, reminders, weather, world time, quick answers, Spotify (see the Tools tab).\n"
            "• Voice & Chat tab: type commands or watch the conversation. Hardware tab: calibrate every servo.")).pack(anchor=tk.W)

    def _build_hardware_extras(self, tab: ttk.Frame) -> None:
        mf = ttk.LabelFrame(tab, text="Mouth (lip-sync calibration)", padding=6)
        mf.pack(fill=tk.X, padx=6, pady=6)
        lo, hi = SERVO_CONFIG["mouth"]["angle_min"], SERVO_CONFIG["mouth"]["angle_max"]
        self.mouth_closed_var = tk.DoubleVar(value=MOUTH_CONFIG["closed"])
        self.mouth_open_var = tk.DoubleVar(value=MOUTH_CONFIG["open"])
        ttk.Label(mf, text="Closed °").grid(row=0, column=0, sticky=tk.W)
        ttk.Scale(mf, from_=lo, to=hi, variable=self.mouth_closed_var, orient="horizontal", length=240,
                  command=lambda v: self._on_mouth_limits()).grid(row=0, column=1, padx=6)
        ttk.Label(mf, text="Open °").grid(row=1, column=0, sticky=tk.W)
        ttk.Scale(mf, from_=lo, to=hi, variable=self.mouth_open_var, orient="horizontal", length=240,
                  command=lambda v: self._on_mouth_limits()).grid(row=1, column=1, padx=6)
        self.mouth_limit_label = ttk.Label(mf, text="")
        self.mouth_limit_label.grid(row=0, column=2, rowspan=2, padx=8)
        ttk.Button(mf, text="Test mouth", command=self.mouth_test).grid(row=2, column=0, pady=4)
        ttk.Button(mf, text="Say test phrase", command=lambda: self.say_text("Testing, one two three. Is my mouth moving with my voice?")).grid(row=2, column=1, sticky=tk.W, pady=4)
        self._on_mouth_limits()

    def _on_mouth_limits(self) -> None:
        lo, hi = SERVO_CONFIG["mouth"]["angle_min"], SERVO_CONFIG["mouth"]["angle_max"]
        closed = max(lo, min(hi - 5, round(self.mouth_closed_var.get())))
        opened = max(closed + 5, min(hi, round(self.mouth_open_var.get())))
        MOUTH_CONFIG["closed"], MOUTH_CONFIG["open"] = float(closed), float(opened)
        self.mouth_limit_label.config(text=f"closed {closed:g}°  open {opened:g}°\n(this session; set SAINT_MOUTH_CLOSED/OPEN to keep)")

    def _build_tools(self, tab: ttk.Frame) -> None:
        def row(title, build):
            f = ttk.LabelFrame(tab, text=title, padding=6)
            f.pack(fill=tk.X, padx=6, pady=4)
            build(f)
        self.tool_mins = tk.StringVar(value="1")
        self.tool_msg = tk.StringVar(value="")
        self.tool_loc = tk.StringVar(value="")
        self.tool_q = tk.StringVar(value="")
        self.tool_song = tk.StringVar(value="")

        def timers(f):
            ttk.Label(f, text="Minutes:").pack(side=tk.LEFT)
            ttk.Entry(f, textvariable=self.tool_mins, width=5).pack(side=tk.LEFT, padx=4)
            ttk.Label(f, text="Note:").pack(side=tk.LEFT)
            ttk.Entry(f, textvariable=self.tool_msg, width=24).pack(side=tk.LEFT, padx=4)
            ttk.Button(f, text="Start timer", command=self.tool_timer).pack(side=tk.LEFT, padx=4)
            ttk.Button(f, text="Set reminder", command=self.tool_reminder).pack(side=tk.LEFT, padx=4)
        row("Timers & reminders", timers)

        def lookups(f):
            ttk.Label(f, text="Place (blank = here):").pack(side=tk.LEFT)
            ttk.Entry(f, textvariable=self.tool_loc, width=22).pack(side=tk.LEFT, padx=4)
            ttk.Button(f, text="Weather", command=lambda: self.tool_say(f"weather in {self.tool_loc.get().strip()}".strip() if self.tool_loc.get().strip() else "weather")).pack(side=tk.LEFT, padx=4)
            ttk.Button(f, text="Time", command=lambda: self.tool_say(f"what is the time in {self.tool_loc.get().strip()}" if self.tool_loc.get().strip() else "what time is it")).pack(side=tk.LEFT, padx=4)
        row("Weather & time", lookups)

        def qa(f):
            e = ttk.Entry(f, textvariable=self.tool_q, width=44)
            e.pack(side=tk.LEFT, padx=4)
            e.bind("<Return>", lambda ev: self.tool_ask())
            ttk.Button(f, text="Ask", command=self.tool_ask).pack(side=tk.LEFT, padx=4)
        row("Ask a question (what is / who is / how many…)", qa)

        def music(f):
            ttk.Entry(f, textvariable=self.tool_song, width=26).pack(side=tk.LEFT, padx=4)
            ttk.Button(f, text="Play", command=lambda: self.tool_say(f"play {self.tool_song.get().strip()} on spotify")).pack(side=tk.LEFT, padx=2)
            for label, cmd in (("⏸", "pause spotify"), ("▶", "resume spotify"), ("⏭", "next on spotify"), ("⏮", "previous on spotify")):
                ttk.Button(f, text=label, width=3, command=lambda c=cmd: self.tool_say(c)).pack(side=tk.LEFT, padx=2)
            ttk.Button(f, text="Spotify Login", command=self.spotify_login).pack(side=tk.LEFT, padx=8)
        row("Music (Spotify)", music)

        out = ttk.LabelFrame(tab, text="Result", padding=6)
        out.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.tool_out = scrolledtext.ScrolledText(out, height=8, wrap=tk.WORD, font=("Helvetica", 11), cursor="arrow")
        self.tool_out.pack(fill=tk.BOTH, expand=True)
        self.tool_out.bind("<Key>", lambda e: None if (e.state & 0x4 and e.keysym.lower() == "c") else "break")

    def _tool_show(self, text: str) -> None:
        def apply():
            self.tool_out.insert(tk.END, f"{datetime.now().strftime('%H:%M')}  {text}\n\n")
            self.tool_out.see(tk.END)
        self._ui(apply)

    def tool_say(self, command: str) -> None:
        """Run a tool through the normal command pipeline (it speaks its own answer)."""
        def work():
            try:
                res = process_command(command)
                self._tool_show(str(res.get("assistant_text") or res.get("action_result") or res.get("error") or "Done"))
                self.display_result(res)
            except Exception as e:
                self._tool_show(f"⚠ {e}")
        threading.Thread(target=work, daemon=True).start()

    def tool_timer(self) -> None:
        try:
            mins = float(self.tool_mins.get())
            if mins <= 0:
                raise ValueError
        except Exception:
            self._tool_show("⚠ Enter minutes as a positive number")
            return
        label = self.tool_msg.get().strip()
        self.tool_say(f"set a timer for {max(1, int(round(mins * 60)))} seconds")
        if label:
            self._tool_show(f"(timer note: {label})")

    def tool_reminder(self) -> None:
        try:
            mins = float(self.tool_mins.get())
            if mins <= 0:
                raise ValueError
        except Exception:
            self._tool_show("⚠ Enter minutes as a positive number")
            return
        msg = self.tool_msg.get().strip() or "Reminder"
        res = schedule_reminder_in(int(round(mins * 60)), msg)
        self._tool_show(f"Reminder set for {mins:g} min: {msg}" if res.get("ok") else f"⚠ {res}")

    def tool_ask(self) -> None:
        q = self.tool_q.get().strip()
        if not q:
            return
        def work():
            res = answer_question(q)
            text = res.get("answer") if res.get("ok") else f"⚠ {res.get('error')}"
            self._tool_show(f"Q: {q}\n{text}")
            if res.get("ok") and AGENT:
                AGENT.tts_say(split_sentences(text)[0] if text else "")
        threading.Thread(target=work, daemon=True).start()

    # -- showcase actions --
    def say_text(self, text: str) -> None:
        if AGENT:
            AGENT.tts_say(text)
        self.display_result({"assistant_text": text})

    def say_hello(self) -> None:
        self.say_text("Hello! I'm SAINT. It's nice to meet you.")

    def introduce(self) -> None:
        self.say_text("I'm SAINT, a voice assistant that lives on this Raspberry Pi. I can set timers, check the weather, "
                      "answer questions, play music, and I look around while I listen. Ask me anything.")

    def look_around_now(self) -> None:
        if demo_is_running():
            self.display_result({"error": "Demo Mode is running. Stop the demo first."})
        elif not (GAZE and GAZE.glance_now()):
            self.display_result({"error": "Turn on 'Ambient look-around' first"})

    def mouth_test(self) -> None:
        if AGENT and AGENT.mouth:
            self._run_bg(AGENT.mouth.test_pattern, "mouth_test")
        else:
            self.display_result({"error": "No servo controller; mouth unavailable"})

    def _on_gaze_toggle(self) -> None:
        if GAZE:
            GAZE.set_enabled(self.gaze_var.get())
        self.display_result({"status": "Ambient look-around on" if self.gaze_var.get() else "Ambient look-around off"})

    def _refresh_live(self) -> None:
        if not CONTROLLER or not hasattr(self, "live_mode"):
            return
        parts = []
        if demo_is_running():
            parts.append(f"Demo · {DEMO.mood}")
        elif GAZE and GAZE.enabled:
            parts.append({"idle": "Looking around", "talk": "Talking", "attend": "Listening to you", "off": "Gaze paused"}.get(GAZE.mode, GAZE.mode))
        else:
            parts.append("Look-around off")
        self.live_mode.config(text=" · ".join(parts))
        m = getattr(AGENT, "mouth", None) if AGENT else None
        self.mouth_bar["value"] = 100 * (m.level if m else 0.0)
        st = CONTROLLER.state
        fmt = lambda n: "--" if st[n]["angle"] is None else f"{st[n]['angle']:5.1f}"
        self.live_angles.config(text=f"eyes L/R {fmt('eyes_lr')}  U/D {fmt('eyes_ud')}   head pan {fmt('pan')}  tilt {fmt('tilt')}   mouth {fmt('mouth')}")

    # -- thread-safe UI helpers --
    def _ui(self, fn) -> None:
        # Never call Tk from another thread (root.after from a worker blocks until
        # the Tk thread is free, which deadlocks while Stop joins the demo thread).
        if threading.current_thread() is threading.main_thread():
            fn()
        else:
            self._ui_queue.put(fn)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                print("[GUI] update error:", e)
        if not self._closing:
            self.root.after(50, self._drain_ui_queue)

    def _run_bg(self, fn, label: str) -> None:
        """Run a (possibly slow, slewed) hardware action off the Tk thread."""
        def work():
            try:
                fn()
            except Exception as e:
                self.display_result({"error": f"{label}: {e}"})
        threading.Thread(target=work, daemon=True).start()

    def _set_motion_enabled(self, enabled: bool) -> None:
        for w in self._motion_widgets:
            try:
                if isinstance(w, ttk.Widget):
                    w.state(["!disabled"] if enabled else ["disabled"])
                    if enabled and isinstance(w, ttk.Combobox):
                        w.state(["readonly"])
                else:
                    w.config(state=tk.NORMAL if enabled else tk.DISABLED)
            except Exception:
                pass
        self.start_demo_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def _on_mousewheel(self, event):
        """Scroll the page with the wheel/touchpad (Windows/macOS: MouseWheel, X11: Button-4/5).
        The chat log and other toplevels scroll themselves."""
        try:
            w = event.widget
            if not isinstance(w, tk.Misc) or w.winfo_toplevel() is not self.root:
                return
            while w is not None:
                if isinstance(w, (tk.Text, tk.Listbox)):
                    return
                w = w.master
        except Exception:
            return
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            d = event.delta
            step = -int(d / 120) * 3 if abs(d) >= 120 else (-1 if d > 0 else 1)
        try:
            canvas = self._tab_canvases.get(self.notebook.select())
        except Exception:
            canvas = None
        if canvas is not None:
            canvas.yview_scroll(step, "units")

    def _refresh_header(self) -> None:
        if self._demo_state == "running":
            text = "Demo running"
        elif self._demo_state == "stopping":
            text = "Stopping"
        elif self._demo_state == "error":
            text = "Error"
        else:
            text = self._voice_status.rstrip(". ") or "Idle"
        bg, fg = self.HEADER_COLORS.get(text, self.HEADER_COLORS["Idle"])
        shown = f"Demo error: {self._demo_error}" if text == "Error" and self._demo_error else text
        if text == "Demo running" and DEMO:
            shown += f"  ·  {DEMO.mood}"
        self.header.config(text=shown + ("   [SIMULATION]" if CONTROLLER and CONTROLLER.pi is None else ""), bg=bg, fg=fg)

    def _on_demo_state(self, state: str) -> None:
        self._demo_state = state
        self._set_motion_enabled(state in ("idle", "error"))
        self._refresh_header()

    def _on_demo_error(self, err: BaseException) -> None:
        self._demo_error = str(err)
        self.display_result({"error": f"Demo stopped after an error: {err}"})

    def _on_servo_selected(self) -> None:
        name = self.servo_var.get()
        if not CONTROLLER or name not in SERVO_CONFIG:
            return
        lo, hi = CONTROLLER.hard_range(name)
        cur = CONTROLLER.state[name]["angle"]
        self.angle_scale.config(from_=lo, to=hi)
        self.angle_var.set(int(round(cur if cur is not None else SERVO_SAFETY[name]["neutral"])))
        self.range_label.config(text=f"safe {lo:g}–{hi:g}°")

    def _update_readout(self) -> None:
        if CONTROLLER:
            for name, lbl in self.angle_labels.items():
                a = CONTROLLER.state[name]["angle"]
                lbl.config(text="--" if a is None else f"{a:6.1f}°")
            rs = CONTROLLER.rgb_state
            if demo_is_running():
                self.swatch.config(bg="#%02x%02x%02x" % (rs["r"], rs["g"], rs["b"]))
                self._refresh_header()  # shows the current mood
        self._refresh_live()
        self.root.after(150, self._update_readout)

    def _update_swatch(self) -> None:
        self.swatch.config(bg="#%02x%02x%02x" % (self.r_var.get(), self.g_var.get(), self.b_var.get()))

    # -- demo / safety --
    def start_demo(self) -> None:
        if not DEMO:
            self.display_result({"error": "No servo controller; demo unavailable"})
            return
        self._demo_error = None
        if DEMO.start():
            self.display_result({"status": "Demo started"})
        else:
            self.display_result({"status": "Demo already running"})

    def stop_demo(self) -> None:
        if DEMO and DEMO.running:
            ok = DEMO.stop(timeout=1.0)
            self.display_result({"status": "Demo stopped" if ok else "Demo did not stop within 1 s"})

    def emergency_stop(self) -> None:
        """Stop the demo, cut any slew short, then glide back to neutral."""
        if DEMO:
            DEMO.stop(timeout=1.0)
        gaze_yield(10.0)
        if AGENT and AGENT.mouth:
            AGENT.mouth.stop()
        if CONTROLLER:
            CONTROLLER.abort_motion()
            self.set_status("Returning to neutral")
            def back():
                CONTROLLER.go_neutral(only_known=True)
                self.set_status("Idle")
            self._run_bg(back, "neutral")
        self.display_result({"status": "Emergency stop: returning to neutral"})

    def _on_space(self, event):
        if isinstance(event.widget, (tk.Text, tk.Entry, ttk.Entry)):
            return None  # let space type normally in text boxes
        self.stop_demo()
        return "break"

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._voice_status = "Stopping"
        self._refresh_header()
        self.root.update_idletasks()
        try:
            self.wake_listener.running = False
        except Exception:
            pass
        shutdown_motion()
        self.root.destroy()

    def _on_mute_mic(self):
        global MUTE_MIC
        MUTE_MIC = self.mute_mic_var.get()
        self.display_result({"status": "Microphone muted: SAINT is not listening" if MUTE_MIC else "Microphone on"})
        self._update_status(reschedule=False)

    def _on_mute_speaker(self):
        global MUTE_SPEAKER
        MUTE_SPEAKER = self.mute_speaker_var.get()
        if MUTE_SPEAKER and AGENT:
            AGENT.tts_stop()  # silence anything already playing
        self.display_result({"status": "Speaker muted" if MUTE_SPEAKER else "Speaker on"})
        self._update_status(reschedule=False)

    def set_status(self, text: str) -> None:
        def apply():
            try:
                self._voice_status = text
                self.status_bar.config(text=text)
                self._refresh_header()
            except Exception:
                pass
        self._ui(apply)

    @staticmethod
    def format_result(obj: Any):
        """Turn a result dict into chat-style (text, tag) lines."""
        if not isinstance(obj, dict):
            return [(str(obj), "info")]
        lines = []
        if obj.get("user_input"):
            lines.append((f"You: {obj['user_input']}", "you"))
        reply = obj.get("assistant_text") or obj.get("assistant")
        if reply:
            lines.append((f"SAINT: {reply}", "saint"))
        if obj.get("action") and obj.get("action_result") and obj.get("action_result") != reply:
            ok = obj.get("action_success")
            lines.append((f"   ↳ {obj['action']}: {obj['action_result']}", "info" if ok is not False else "error"))
        if obj.get("error"):
            lines.append((f"⚠ {obj['error']}", "error"))
        if obj.get("reminder"):
            lines.append((f"⏰ Reminder: {obj['reminder']} ({obj.get('time', '')})", "saint"))
        if not lines:
            rest = ", ".join(f"{k}: {v}" for k, v in obj.items())
            lines.append((f"• {rest}", "info"))
        return lines

    def display_result(self, obj: Any) -> None:
        def apply():
            try:
                stamp = datetime.now().strftime("%H:%M")
                for text, tag in self.format_result(obj):
                    if tag == "you":
                        self.log_text.insert(tk.END, f"You · {stamp}\n", "meta_you")
                        self.log_text.insert(tk.END, f" {text[len('You: '):]} \n", "you")
                    elif tag == "saint":
                        self.log_text.insert(tk.END, f"SAINT · {stamp}\n", "meta_saint")
                        self.log_text.insert(tk.END, f" {text[len('SAINT: '):]} \n", "saint")
                    else:
                        self.log_text.insert(tk.END, f"{stamp}  {text}\n", tag)
                self.log_text.see(tk.END)
            except Exception:
                pass
        self._ui(apply)

    def login_prompt(self):
        token = simpledialog.askstring("SAINT Login", "Enter SAINT token:", show="*")
        if token and token == SAINT_TOKEN:
            messagebox.showinfo("SAINT", "Authenticated")
        else:
            messagebox.showerror("SAINT", "Invalid token")

    def spotify_login(self):
        if not SPOT or not SPOTIFY_CLIENT_ID:
            messagebox.showinfo("Spotify", "Spotify not configured")
            return
        import webbrowser
        url = SPOT.get_auth_url()
        webbrowser.open(url)
        messagebox.showinfo("Spotify", "Browser opened. Complete authorization and return.")

    def execute_command(self):
        text = self.cmd_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.cmd_text.delete("1.0", tk.END)
        self.set_status("Executing...")
        def work():
            try:
                result = process_command(text)
                self.display_result(result)
            except Exception as e:
                self.display_result({"error": str(e)})
            finally:
                self.set_status("Idle")
        # off the Tk thread: lookups and slewed neck moves can take a while
        threading.Thread(target=work, daemon=True).start()

    def _motion_allowed(self) -> bool:
        if demo_is_running():
            self.display_result({"error": "Demo Mode is running. Stop the demo first."})
            return False
        return bool(CONTROLLER)

    def set_servo(self):
        servo = self.servo_var.get()
        try:
            angle = float(self.angle_var.get())
        except Exception:
            self.display_result({"error": "Angle must be a number"})
            return
        if not self._motion_allowed():
            return
        gaze_yield()
        def move():
            CONTROLLER.set_angle(servo, angle)
            actual = CONTROLLER.state[servo]["angle"]
            note = "" if actual == angle else f" (limited from {angle:g}°)"
            self.display_result({"action": "set_servo", "action_result": f"{servo} → {actual:g}°{note}"})
        self._run_bg(move, "set_servo")

    def center_all(self):
        if not self._motion_allowed():
            return
        gaze_yield()
        def move():
            CONTROLLER.go_neutral()
            self.display_result({"action": "center_all", "action_result": "all servos at neutral"})
        self._run_bg(move, "center_all")

    def set_led(self, rgb=None):
        if rgb is not None:
            self.r_var.set(rgb[0]); self.g_var.set(rgb[1]); self.b_var.set(rgb[2])
        r,g,b = int(self.r_var.get()), int(self.g_var.get()), int(self.b_var.get())
        self._update_swatch()
        if not self._motion_allowed():
            return
        try:
            CONTROLLER.rgb_set_color(r,g,b)
            self.display_result({"action":"led_set","action_result":f"eyes RGB({r},{g},{b})"})
        except Exception as e:
            self.display_result({"error": str(e)})

    def led_off(self):
        if not self._motion_allowed():
            return
        try:
            CONTROLLER.rgb_off()
            self.display_result({"action":"led_off","action_result":"eyes off"})
        except Exception as e:
            self.display_result({"error": str(e)})

    def show_history(self):
        win = tk.Toplevel(self.root)
        win.title("Conversation History")
        txt = scrolledtext.ScrolledText(win)
        txt.pack(fill=tk.BOTH, expand=True)
        with HISTORY_LOCK:
            for e in CONVERSATION_HISTORY[-500:]:
                ts = e.get("time","")
                role = e.get("role","")
                txt.insert(tk.END, f"[{ts}] {role}: {e.get('text')}\n\n")
        txt.configure(state=tk.DISABLED)

    def open_countdown_window(self, seconds: int, message: str):
        win = tk.Toplevel(self.root)
        win.title(f"Timer: {message}")
        lbl = ttk.Label(win, text="", font=("Helvetica", 20))
        lbl.pack(padx=12, pady=12)
        end_time = time.time() + seconds
        def update():
            remaining = int(round(end_time - time.time()))
            if remaining <= 0:
                lbl.config(text="00:00")
                try:
                    AGENT.tts_say(f"Timer finished: {message}")
                except Exception:
                    pass
                notify_now(f"Timer finished: {message}")
                # close after a brief pause
                win.after(800, win.destroy)
                return
            mins, secs = divmod(remaining, 60)
            lbl.config(text=f"{mins:02d}:{secs:02d}")
            win.after(250, update)
        update()

    def _update_status(self, reschedule: bool = True):
        speaking = bool(AGENT and getattr(AGENT, "is_speaking", False))
        parts = ["SAINT"]
        parts.append("Mic muted" if MUTE_MIC else ("Mic paused (speaking)" if speaking else "Mic on"))
        parts.append("Speaker muted" if MUTE_SPEAKER else "Speaker on")
        if AGENT:
            parts.append(f"Voice: {getattr(AGENT, 'backend', '?')}")
        parts.append("LLM ready" if llm else "LLM off")
        self.status_label.config(text=" | ".join(parts))
        if reschedule:
            self.root.after(1000, self._update_status)

# ------------ Flask server for Spotify callback (runs in background) ------------
app = Flask("saint_spotify")
app.secret_key = os.urandom(24).hex()

@app.route("/spotify/callback")
def spotify_callback():
    if not SPOT:
        return "Spotify not configured", 500
    result = SPOT.handle_callback(request.args)
    if result.get("ok"):
        html = "<html><body><h2>Spotify Authorization Complete!</h2><p>You can close this window.</p></body></html>"
        print("[SPOTIFY] Authorization complete")
        return html
    else:
        return f"<html><body><h3>Authorization failed: {result}</h3></body></html>", 400

def run_flask():
    try:
        app.run(host="127.0.0.1", port=8765, debug=False, use_reloader=False)
    except Exception as e:
        print("[FLASK ERROR]", e)

# ------------ Local LLM (optional) ------------
def init_local_llm():
    global llm
    if not HAS_LLAMA_CPP:
        print("[LLM] llama-cpp-python not installed")
        return False
    if not MODEL_PATH or not os.path.exists(MODEL_PATH):
        print("[LLM] Model path not found; skipping LLM init")
        return False
    try:
        llm = Llama(model_path=MODEL_PATH, n_ctx=2048, n_threads=max(1, os.cpu_count() or 1), verbose=False)
        print("[LLM] loaded")
        return True
    except Exception as e:
        print("[LLM] load error", e)
        llm = None
        return False

def generate_text(prompt: str, max_tokens: int=128) -> str:
    if not llm:
        return "[LLM not available]"
    try:
        out = llm(prompt, max_tokens=max_tokens, temperature=0.7)
        if isinstance(out, dict):
            choices = out.get("choices")
            if choices and isinstance(choices, list) and choices[0]:
                return choices[0].get("text","").strip()
        return str(out)
    except Exception as e:
        return f"[LLM error: {e}]"

# ------------ Conversation (small talk -> local LLM -> fallback) ------------
SAINT_PERSONA = (
    "You are SAINT, a small robot head that sits on a desk. You have moving eyes, a neck "
    "and glowing RGB eyes, and you run on a Raspberry Pi. You are friendly, curious, a bit "
    "witty and direct. Answer in one or two short sentences that sound natural when spoken "
    "aloud: no lists, no markdown, no emojis. If you don't know, say so briefly."
)

SMALL_TALK = [
    (r"^(hi|hello|hey|yo|good (morning|afternoon|evening))\b", [
        "Hey! Good to see you.", "Hello there. What can I do for you?", "Hi! I'm all ears. Well, all eyes."]),
    (r"\bhow are you\b|\bhow's it going\b|\bhow are things\b", [
        "Running smoothly, thanks. How about you?", "All systems happy. What's new with you?"]),
    (r"\bwho are you\b|\bwhat(?:'s| is) your name\b|\bwhat are you\b", [
        "I'm SAINT, a desktop robot head. I can look around, change my eye colour, set timers and answer questions."]),
    (r"\bwhat can you do\b|\bhelp\b", [
        "I can set timers and reminders, check the weather and time, answer questions, play Spotify, "
        "change my eye colour and look around. Try: look left, or set eyes to purple."]),
    (r"\b(thanks|thank you|cheers)\b", ["Any time.", "You're welcome.", "Happy to help."]),
    (r"\b(bye|goodbye|see you|good night)\b", ["See you later.", "Bye for now. I'll keep an eye out."]),
    (r"\bjoke\b", [
        "Why did the robot go on holiday? It needed to recharge.",
        "I told my servo a joke. It didn't move it, but it turned its head.",
        "I'd tell you a UDP joke, but you might not get it."]),
    (r"\b(you're|you are) (cool|awesome|great|smart|cute)\b", ["Thanks! My eyes are glowing a little brighter.", "Appreciate it."]),
]

def is_small_talk(text: str) -> bool:
    tl = text.strip().lower()
    return any(re.search(p, tl) for p, _ in SMALL_TALK)

def small_talk_reply(text: str) -> Optional[str]:
    tl = text.strip().lower()
    for pattern, replies in SMALL_TALK:
        if re.search(pattern, tl):
            return random.choice(replies)
    return None

def recent_history_messages(limit: int = 6) -> list:
    with HISTORY_LOCK:
        items = list(CONVERSATION_HISTORY[-limit:])
    return [{"role": "user" if e.get("role") == "user" else "assistant", "content": str(e.get("text", ""))[:400]}
            for e in items if e.get("text")]

def clean_reply(text: str, max_sentences: int = 2, max_chars: int = 300) -> str:
    """Tidy LLM output for speech: no markdown or role prefixes, at most two sentences."""
    t = re.split(r"\n\s*(?:user|you|human)\s*:", str(text), flags=re.I)[0]
    t = re.sub(r"[*_#`>|]+", "", t)
    t = re.sub(r"^\s*(saint|assistant)\s*:\s*", "", t, flags=re.I)
    t = " ".join(t.split())
    sentences = re.split(r"(?<=[.!?])\s+", t)
    t = " ".join(sentences[:max_sentences]).strip()
    if len(t) > max_chars:
        t = t[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return t

def generate_chat(user_text: str, max_tokens: int = 80) -> Optional[str]:
    """Ask the local LLM for a reply with the SAINT persona and recent history."""
    if not llm:
        return None
    messages = [{"role": "system", "content": SAINT_PERSONA}] + recent_history_messages() + \
               [{"role": "user", "content": user_text}]
    try:
        out = llm.create_chat_completion(messages=messages, max_tokens=max_tokens, temperature=0.7, top_p=0.9)
        reply = out["choices"][0]["message"]["content"]
    except Exception as e:
        print("[LLM] chat completion failed, trying plain prompt:", e)
        convo = "\n".join(f"{'User' if m['role'] == 'user' else 'SAINT'}: {m['content']}" for m in messages[1:])
        reply = generate_text(f"{SAINT_PERSONA}\n\n{convo}\nSAINT:", max_tokens=max_tokens)
        if reply.startswith("[LLM"):
            return None
    reply = clean_reply(reply)
    return reply or None

def chat_reply(text: str) -> Optional[str]:
    """Fast canned small talk first (the Pi's LLM takes seconds), then the LLM."""
    return small_talk_reply(text) or generate_chat(text)

# ------------ Spotify Controller ------------
class SpotifyController:
    def __init__(self, client_id, client_secret, redirect_uri, scopes=SPOTIFY_SCOPES):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.state = None
        self.access_token = None
        self.refresh_token = None
        self.token_expires_at = 0

    def _make_auth_header(self):
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return {"Authorization": "Basic " + base64.b64encode(raw).decode("utf-8")}

    def get_auth_url(self):
        self.state = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(16))
        qs = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": self.state,
            "show_dialog": "true"
        }
        return "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(qs)

    def handle_callback(self, params):
        code = params.get("code")
        state = params.get("state")
        error = params.get("error")
        if error:
            return {"ok": False, "error": error}
        if not code:
            return {"ok": False, "error": "no code"}
        return self.exchange_code_for_token(code)

    def exchange_code_for_token(self, code):
        url = "https://accounts.spotify.com/api/token"
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri}
        try:
            headers = self._make_auth_header()
            r = requests.post(url, data=data, headers=headers, timeout=10)
            if r.status_code != 200:
                return {"ok": False, "status": r.status_code, "body": r.text}
            j = r.json()
            self.access_token = j.get("access_token")
            self.refresh_token = j.get("refresh_token")
            self.token_expires_at = time.time() + j.get("expires_in", 3600) - 10
            return {"ok": True, "data": j}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def refresh_access_token(self):
        if not self.refresh_token:
            return False
        url = "https://accounts.spotify.com/api/token"
        data = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        try:
            headers = self._make_auth_header()
            r = requests.post(url, data=data, headers=headers, timeout=10)
            if r.status_code != 200:
                return False
            j = r.json()
            self.access_token = j.get("access_token")
            self.token_expires_at = time.time() + j.get("expires_in", 3600) - 10
            if j.get("refresh_token"):
                self.refresh_token = j["refresh_token"]
            return True
        except Exception:
            return False

    def _ensure_token(self):
        if not self.access_token:
            return False
        if time.time() > self.token_expires_at - 5:
            return self.refresh_access_token()
        return True

    def api_request(self, method, path, params=None, json_body=None):
        if not self._ensure_token():
            return {"ok": False, "error": "no valid token"}
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        url = "https://api.spotify.com/v1" + path
        try:
            r = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=10)
            if r.status_code in (200, 201, 204):
                if r.status_code == 204 or not r.text:
                    return {"ok": True, "status": r.status_code, "data": None}
                return {"ok": True, "status": r.status_code, "data": r.json()}
            return {"ok": False, "status": r.status_code, "body": r.text}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pause(self):
        return self.api_request("PUT", "/me/player/pause")

    def resume(self):
        return self.api_request("PUT", "/me/player/play")

    def next_track(self):
        return self.api_request("POST", "/me/player/next")

    def previous_track(self):
        return self.api_request("POST", "/me/player/previous")

    def play(self, query: str):
        if not query:
            return {"ok": False, "error": "no query"}
        res = self.api_request("GET", "/search", params={"q": query, "type": "track", "limit": 1})
        if not res.get("ok"):
            return {"ok": False, "error": "search_failed"}
        tracks = (res.get("data") or {}).get("tracks", {}).get("items", [])
        if not tracks:
            return {"ok": False, "error": "no_tracks_found"}
        uri = tracks[0]["uri"]
        return self.api_request("PUT", "/me/player/play", json_body={"uris": [uri]})

# ------------ Shutdown ------------
_SHUTDOWN_DONE = False

def shutdown_motion() -> None:
    """Stop the demo, return moved servos slowly to neutral. Safe to call twice."""
    global _SHUTDOWN_DONE
    if _SHUTDOWN_DONE:
        return
    _SHUTDOWN_DONE = True
    try:
        if GAZE:
            GAZE.stop(timeout=1.0)
        if AGENT and getattr(AGENT, "mouth", None):
            AGENT.mouth.stop()
        if DEMO and not DEMO.stop(timeout=1.0):
            print("[SHUTDOWN] demo thread did not stop within 1 s")
        if CONTROLLER:
            CONTROLLER.abort_motion()
            CONTROLLER.go_neutral(only_known=True)
    except Exception as e:
        print("[SHUTDOWN] return to neutral failed:", e)

# ------------ Main entrypoint ------------
def main():
    global CONTROLLER, AGENT, SPOT, RHINO, DEMO, GAZE
    print("="*60)
    print("SAINT - ONNX Wake Word (Enhanced)")
    print("="*60)
    print(f"Wake model: {WAKE_MODEL_PATH}")
    print(f"Sample rate: {WAKE_SAMPLE_RATE} Hz; Window: {WAKE_WINDOW_MS} ms")
    print(f"Threshold: {WAKE_THRESHOLD}; Cooldown: {WAKE_COOLDOWN}")
    print(f"LLM model path: {MODEL_PATH} (llama-cpp-python {'available' if HAS_LLAMA_CPP else 'not installed'})")
    print(f"TTS available: {HAS_TTS}; SpeechRecognition: {HAS_SR}; pigpio: {PIGPIO_AVAILABLE}; onnxruntime: {bool(ort)}")
    print(f"Simulate: {SIMULATE}; Demo neck moves: {DEMO_CONFIG['neck_enabled']}")
    print("="*60)

    CONTROLLER = ServoController(simulate=SIMULATE)
    DEMO = DemoController(CONTROLLER)
    GAZE = GazeController(CONTROLLER, enabled=GAZE_ENABLED)
    AGENT = Agent()
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        SPOT = SpotifyController(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI)

    if HAS_RHINO and os.path.exists(RHINO_CONTEXT_PATH) and PICOVOICE_ACCESS_KEY:
        RHINO = RhinoIntentRecognizer(RHINO_CONTEXT_PATH, PICOVOICE_ACCESS_KEY)

    init_local_llm()

    ensure_reminders_db_exists()
    load_and_schedule_reminders()

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    try:
        root = tk.Tk()
        gui = SaintGUI(root)
        GAZE.start()
        print("[GUI] ready")
        root.mainloop()
    except KeyboardInterrupt:
        print("Interrupted; shutting down")
    finally:
        shutdown_motion()
        if CONTROLLER:
            CONTROLLER.cleanup()
        if RHINO:
            RHINO.cleanup()

if __name__ == "__main__":
    try:
        import numpy as np  # re-import to ensure variable present
    except Exception:
        print("Warning: numpy not installed; wake model requires numpy + onnxruntime.")
    main()
