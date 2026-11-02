// Mouse, keyboard and touch -> Halo Remote "control" messages. Pure functions and small state machines,
// no DOM access, so they run under `node --test` (web/remote/test/input.test.mjs).

// KeyboardEvent.code -> Linux evdev keycode (linux/input-event-codes.h). Layout-independent: the PC's
// keyboard layout decides the character, exactly as for a USB keyboard.
export const KEYMAP = {
  Escape: 1, Digit1: 2, Digit2: 3, Digit3: 4, Digit4: 5, Digit5: 6, Digit6: 7, Digit7: 8, Digit8: 9, Digit9: 10,
  Digit0: 11, Minus: 12, Equal: 13, Backspace: 14, Tab: 15, KeyQ: 16, KeyW: 17, KeyE: 18, KeyR: 19, KeyT: 20,
  KeyY: 21, KeyU: 22, KeyI: 23, KeyO: 24, KeyP: 25, BracketLeft: 26, BracketRight: 27, Enter: 28, ControlLeft: 29,
  KeyA: 30, KeyS: 31, KeyD: 32, KeyF: 33, KeyG: 34, KeyH: 35, KeyJ: 36, KeyK: 37, KeyL: 38, Semicolon: 39,
  Quote: 40, Backquote: 41, ShiftLeft: 42, Backslash: 43, KeyZ: 44, KeyX: 45, KeyC: 46, KeyV: 47, KeyB: 48,
  KeyN: 49, KeyM: 50, Comma: 51, Period: 52, Slash: 53, ShiftRight: 54, NumpadMultiply: 55, AltLeft: 56,
  Space: 57, CapsLock: 58, F1: 59, F2: 60, F3: 61, F4: 62, F5: 63, F6: 64, F7: 65, F8: 66, F9: 67, F10: 68,
  NumLock: 69, ScrollLock: 70, Numpad7: 71, Numpad8: 72, Numpad9: 73, NumpadSubtract: 74, Numpad4: 75,
  Numpad5: 76, Numpad6: 77, NumpadAdd: 78, Numpad1: 79, Numpad2: 80, Numpad3: 81, Numpad0: 82, NumpadDecimal: 83,
  IntlBackslash: 86, F11: 87, F12: 88, IntlRo: 89, NumpadEnter: 96, ControlRight: 97, NumpadDivide: 98,
  PrintScreen: 99, AltRight: 100, Home: 102, ArrowUp: 103, PageUp: 104, ArrowLeft: 105, ArrowRight: 106,
  End: 107, ArrowDown: 108, PageDown: 109, Insert: 110, Delete: 111, AudioVolumeMute: 113,
  AudioVolumeDown: 114, AudioVolumeUp: 115, NumpadEqual: 117, Pause: 119, IntlYen: 124, MetaLeft: 125,
  MetaRight: 126, ContextMenu: 127,
};

export const keyMessage = (code, down) => (code in KEYMAP ? { type: "key", code: KEYMAP[code], down } : null);

// The video is shown with object-fit: contain, so there may be bars around the picture. Map a point in
// page coordinates to 0..1 of the picture itself.
export function normalizedPoint(px, py, rect, videoW, videoH) {
  if (!videoW || !videoH || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / videoW, rect.height / videoH);
  const w = videoW * scale, h = videoH * scale;
  const left = rect.left + (rect.width - w) / 2, top = rect.top + (rect.height - h) / 2;
  const clamp = (v) => Math.min(Math.max(v, 0), 1);
  return { x: clamp((px - left) / w), y: clamp((py - top) / h) };
}

// MouseEvent.button (0 left, 1 middle, 2 right) -> protocol button (1 left, 2 middle, 3 right)
export const mouseButton = (b) => ({ 0: 1, 1: 2, 2: 3 })[b] ?? null;

// WheelEvent -> pointer.scroll in pixels
export function wheelMessage(deltaX, deltaY, deltaMode) {
  const k = deltaMode === 1 ? 16 : deltaMode === 2 ? 400 : 1;
  const dx = Math.round(deltaX * k), dy = Math.round(deltaY * k);
  return dx || dy ? { type: "pointer.scroll", dx, dy } : null;
}

// ---------------------------------------------------------------- touch
// Direct touch, like on a tablet:
//   tap = left click there · long press = right click · drag = left-button drag
//   two-finger tap = right click · two-finger pan = scroll (or move the zoomed view) · pinch = zoom the view
// feed(): {kind: "start"|"move"|"end"|"cancel", touches: [{id, x, y}] (x/y: 0..1 of the picture, plus px/py in
// screen pixels), t: ms}. tick(t) handles the long press. Both return a list of outputs:
//   {send: <control message>}  or  {view: {zoom, panX, panY}}
export const TAP_SLOP_PX = 10, LONG_PRESS_MS = 550, TAP_MS = 300;

export class TouchGestures {
  constructor() {
    this.zoom = 1; this.panX = 0; this.panY = 0;
    this.reset();
  }
  reset() {
    this.mode = "idle"; // idle | one | drag | two | done
    this.start = null; this.last = null; this.startT = 0; this.maxTouches = 0;
    this.pinch = null; this.pressed = false;
  }
  _view() { return { view: { zoom: this.zoom, panX: this.panX, panY: this.panY } }; }
  _move(p) { return { send: { type: "pointer.move", x: p.x, y: p.y } }; }
  _btn(button, down) { return { send: { type: "pointer.button", button, down } }; }

  feed(ev) {
    const out = [];
    const n = ev.touches.length;
    if (ev.kind === "start") {
      this.maxTouches = Math.max(this.maxTouches, n);
      if (n === 1 && this.mode === "idle") {
        this.mode = "one"; this.start = this.last = ev.touches[0]; this.startT = ev.t;
      } else if (n === 2 && (this.mode === "one" || this.mode === "idle")) {
        const [a, b] = ev.touches;
        if (this.mode === "idle") this.startT = ev.t;
        this.mode = "two";
        this.pinch = { d: Math.hypot(a.px - b.px, a.py - b.py), cx: (a.px + b.px) / 2, cy: (a.py + b.py) / 2,
          zoom: this.zoom, moved: false };
      }
      return out;
    }
    if (ev.kind === "move") {
      if ((this.mode === "one" || this.mode === "drag") && n === 1) {
        const p = ev.touches[0];
        if (this.mode === "one" && Math.hypot(p.px - this.start.px, p.py - this.start.py) > TAP_SLOP_PX) {
          this.mode = "drag";
          out.push(this._move(this.start), this._btn(1, true));
          this.pressed = true;
        }
        if (this.mode === "drag") out.push(this._move(p));
        this.last = p;
      } else if (this.mode === "two" && n === 2) {
        const [a, b] = ev.touches;
        const d = Math.hypot(a.px - b.px, a.py - b.py);
        const cx = (a.px + b.px) / 2, cy = (a.py + b.py) / 2;
        const dx = cx - this.pinch.cx, dy = cy - this.pinch.cy;
        if (Math.abs(d - this.pinch.d) > 24 || this.pinch.zooming) {
          this.pinch.zooming = true;
          this.zoom = Math.min(Math.max(this.pinch.zoom * (d / this.pinch.d), 1), 4);
          if (this.zoom === 1) { this.panX = 0; this.panY = 0; }
          out.push(this._view());
        } else if (Math.hypot(dx, dy) > TAP_SLOP_PX || this.pinch.moved) {
          this.pinch.moved = true;
          if (this.zoom > 1) {
            this.panX += dx; this.panY += dy;
            out.push(this._view());
          } else {
            // natural scrolling: fingers up = content up = scroll down
            out.push({ send: { type: "pointer.scroll", dx: Math.round(-dx * 2) || 0, dy: Math.round(-dy * 2) || 0 } });
          }
        }
        if (this.pinch.moved || this.pinch.zooming) { this.pinch.cx = cx; this.pinch.cy = cy; }
      }
      return out;
    }
    // end / cancel
    if (ev.kind === "cancel") {
      if (this.pressed) out.push(this._btn(1, false));
      this.reset();
      return out;
    }
    if (n > 0) return out; // wait until every finger is up
    const quick = ev.t - this.startT < TAP_MS;
    if (this.mode === "one" && quick) {
      out.push(this._move(this.start), this._btn(1, true), this._btn(1, false));
    } else if (this.mode === "drag" && this.pressed) {
      out.push(this._btn(1, false));
    } else if (this.mode === "two" && this.maxTouches === 2 && !this.pinch.moved && !this.pinch.zooming && quick) {
      out.push(this._btn(3, true), this._btn(3, false));
    }
    this.reset();
    return out;
  }

  tick(t) {
    if (this.mode === "one" && t - this.startT >= LONG_PRESS_MS) {
      this.mode = "done"; // the finger lifting later does nothing more
      return [this._move(this.start), this._btn(3, true), this._btn(3, false)];
    }
    return [];
  }

  resetView() { this.zoom = 1; this.panX = 0; this.panY = 0; return [this._view()]; }
}

// Soft keyboards (iPad/iPhone) give text, not key codes: InputEvent -> messages.
export function softKeyboardMessages(inputType, data) {
  if (inputType === "insertText" || inputType === "insertReplacementText" || inputType === "insertFromPaste") {
    return data ? [{ type: "text", utf8: data }] : [];
  }
  if (inputType === "insertLineBreak" || inputType === "insertParagraph") {
    return [{ type: "key", code: KEYMAP.Enter, down: true }, { type: "key", code: KEYMAP.Enter, down: false }];
  }
  if (inputType === "deleteContentBackward") {
    return [{ type: "key", code: KEYMAP.Backspace, down: true }, { type: "key", code: KEYMAP.Backspace, down: false }];
  }
  return [];
}

// Throttle pointer moves to one per animation frame / interval; the last position always wins.
export class MoveCoalescer {
  constructor(intervalMs = 16) { this.interval = intervalMs; this.lastSent = -Infinity; this.pending = null; }
  push(msg, t) {
    if (t - this.lastSent >= this.interval) { this.lastSent = t; this.pending = null; return msg; }
    this.pending = msg;
    return null;
  }
  flush(t) {
    const m = this.pending;
    if (m) { this.pending = null; this.lastSent = t; }
    return m;
  }
}

// Keep the PC's modifiers in step with the event's flags: a modifier pressed before the page had focus (or
// sent by a synthetic/assistive keyboard without its own keydown) is pressed on the PC, and one released
// elsewhere is released. Returns [{code, down}] to send before the key itself; updates `down` (a Set of codes).
const MODIFIERS = [["ctrlKey", "ControlLeft", "ControlRight"], ["shiftKey", "ShiftLeft", "ShiftRight"],
  ["altKey", "AltLeft", "AltRight"], ["metaKey", "MetaLeft", "MetaRight"]];
export function syncModifiers(flags, down, keyCode) {
  const out = [];
  for (const [flag, left, right] of MODIFIERS) {
    if (keyCode === left || keyCode === right) continue;
    const held = down.has(left) || down.has(right);
    if (flags[flag] && !held) { down.add(left); out.push({ code: left, down: true }); }
    if (!flags[flag] && held) {
      for (const c of [left, right]) if (down.has(c)) { down.delete(c); out.push({ code: c, down: false }); }
    }
  }
  return out;
}
