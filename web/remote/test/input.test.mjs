import assert from "node:assert/strict";
import { test } from "node:test";
import * as I from "../js/input.js";

const pt = (id, px, py, w = 1000, h = 500) => ({ id, px, py, x: px / w, y: py / h });
const sends = (out) => out.filter((o) => o.send).map((o) => o.send);

test("letterboxed video maps to the picture, clamped", () => {
  // 1000x1000 element showing a 16:9 picture: bars top and bottom
  const rect = { left: 0, top: 0, width: 1000, height: 1000 };
  assert.deepEqual(I.normalizedPoint(500, 500, rect, 1920, 1080), { x: 0.5, y: 0.5 });
  const top = (1000 - 562.5) / 2;
  const corner = I.normalizedPoint(0, top, rect, 1920, 1080);
  assert.ok(Math.abs(corner.x) < 1e-9 && Math.abs(corner.y) < 1e-9);
  assert.deepEqual(I.normalizedPoint(-50, 5, rect, 1920, 1080), { x: 0, y: 0 });
  assert.equal(I.normalizedPoint(1, 1, rect, 0, 0), null);
});

test("keyboard codes are evdev codes", () => {
  assert.deepEqual(I.keyMessage("KeyA", true), { type: "key", code: 30, down: true });
  assert.deepEqual(I.keyMessage("ControlLeft", false), { type: "key", code: 29, down: false });
  assert.equal(I.KEYMAP.MetaLeft, 125);
  assert.equal(I.KEYMAP.ArrowDown, 108);
  assert.equal(I.keyMessage("Fn", true), null);
  const codes = Object.values(I.KEYMAP);
  assert.equal(new Set(codes).size, codes.length, "no two keys share a code");
});

test("mouse buttons and wheel", () => {
  assert.equal(I.mouseButton(2), 3);
  assert.equal(I.mouseButton(7), null);
  assert.deepEqual(I.wheelMessage(0, 3, 1), { type: "pointer.scroll", dx: 0, dy: 48 });
  assert.equal(I.wheelMessage(0, 0, 0), null);
});

test("tap clicks where the finger was", () => {
  const g = new I.TouchGestures();
  g.feed({ kind: "start", touches: [pt(1, 100, 100)], t: 0 });
  const out = sends(g.feed({ kind: "end", touches: [], t: 120 }));
  assert.deepEqual(out, [{ type: "pointer.move", x: 0.1, y: 0.2 }, { type: "pointer.button", button: 1, down: true },
    { type: "pointer.button", button: 1, down: false }]);
});

test("long press is a right click, and lifting afterwards adds nothing", () => {
  const g = new I.TouchGestures();
  g.feed({ kind: "start", touches: [pt(1, 100, 100)], t: 0 });
  assert.deepEqual(g.tick(200), []);
  const out = sends(g.tick(600));
  assert.deepEqual(out.slice(1), [{ type: "pointer.button", button: 3, down: true },
    { type: "pointer.button", button: 3, down: false }]);
  assert.deepEqual(g.feed({ kind: "end", touches: [], t: 900 }), []);
});

test("drag holds the left button until the finger lifts", () => {
  const g = new I.TouchGestures();
  g.feed({ kind: "start", touches: [pt(1, 100, 100)], t: 0 });
  assert.deepEqual(g.feed({ kind: "move", touches: [pt(1, 104, 100)], t: 10 }), []); // inside the slop
  const out = sends(g.feed({ kind: "move", touches: [pt(1, 150, 100)], t: 20 }));
  assert.deepEqual(out.map((m) => m.type), ["pointer.move", "pointer.button", "pointer.move"]);
  assert.equal(g.tick(5000).length, 0, "a drag never turns into a long press");
  assert.deepEqual(sends(g.feed({ kind: "end", touches: [], t: 900 })), [{ type: "pointer.button", button: 1, down: false }]);
  // a cancelled drag (incoming call, palm) still releases the button
  g.feed({ kind: "start", touches: [pt(1, 0, 0)], t: 0 });
  g.feed({ kind: "move", touches: [pt(1, 80, 0)], t: 10 });
  assert.deepEqual(sends(g.feed({ kind: "cancel", touches: [], t: 20 })), [{ type: "pointer.button", button: 1, down: false }]);
});

test("two-finger tap is a right click; two-finger pan scrolls", () => {
  const g = new I.TouchGestures();
  g.feed({ kind: "start", touches: [pt(1, 100, 100)], t: 0 });
  g.feed({ kind: "start", touches: [pt(1, 100, 100), pt(2, 200, 100)], t: 20 });
  g.feed({ kind: "end", touches: [pt(2, 200, 100)], t: 100 });
  assert.deepEqual(sends(g.feed({ kind: "end", touches: [], t: 120 })),
    [{ type: "pointer.button", button: 3, down: true }, { type: "pointer.button", button: 3, down: false }]);

  g.feed({ kind: "start", touches: [pt(1, 100, 100), pt(2, 200, 100)], t: 0 });
  const out = sends(g.feed({ kind: "move", touches: [pt(1, 100, 60), pt(2, 200, 60)], t: 50 }));
  assert.deepEqual(out, [{ type: "pointer.scroll", dx: 0, dy: 80 }]);
  assert.deepEqual(g.feed({ kind: "end", touches: [], t: 400 }), []);
});

test("pinch zooms the local view (1x..4x) and pans it while zoomed", () => {
  const g = new I.TouchGestures();
  g.feed({ kind: "start", touches: [pt(1, 400, 200), pt(2, 500, 200)], t: 0 });
  const out = g.feed({ kind: "move", touches: [pt(1, 350, 200), pt(2, 550, 200)], t: 30 });
  assert.equal(out[0].view.zoom, 2);
  assert.equal(sends(out).length, 0, "zooming sends nothing to the PC");
  g.feed({ kind: "end", touches: [], t: 400 });
  g.feed({ kind: "start", touches: [pt(1, 400, 200), pt(2, 500, 200)], t: 500 });
  const pan = g.feed({ kind: "move", touches: [pt(1, 430, 200), pt(2, 530, 200)], t: 530 });
  assert.deepEqual(pan, [{ view: { zoom: 2, panX: 30, panY: 0 } }]);
  assert.deepEqual(g.resetView(), [{ view: { zoom: 1, panX: 0, panY: 0 } }]);
});

test("soft keyboard input becomes text and keys", () => {
  assert.deepEqual(I.softKeyboardMessages("insertText", "é"), [{ type: "text", utf8: "é" }]);
  assert.deepEqual(I.softKeyboardMessages("deleteContentBackward").map((m) => m.code), [14, 14]);
  assert.deepEqual(I.softKeyboardMessages("insertLineBreak").map((m) => m.down), [true, false]);
  assert.deepEqual(I.softKeyboardMessages("formatBold"), []);
});

test("pointer moves are coalesced, the last one wins", () => {
  const c = new I.MoveCoalescer(16);
  assert.ok(c.push({ x: 1 }, 0));
  assert.equal(c.push({ x: 2 }, 5), null);
  assert.equal(c.push({ x: 3 }, 10), null);
  assert.deepEqual(c.flush(16), { x: 3 });
  assert.equal(c.flush(20), null);
});

test("modifiers follow the event flags", () => {
  const down = new Set();
  assert.deepEqual(I.syncModifiers({ ctrlKey: true }, down, "KeyA"), [{ code: "ControlLeft", down: true }]);
  assert.deepEqual(I.syncModifiers({ ctrlKey: true }, down, "KeyA"), []);
  assert.deepEqual(I.syncModifiers({ ctrlKey: true, shiftKey: true }, down, "ShiftLeft"), [], "the key itself isn't doubled");
  down.add("ShiftLeft");
  assert.deepEqual(I.syncModifiers({}, down, "KeyB"), [{ code: "ControlLeft", down: false }, { code: "ShiftLeft", down: false }]);
  assert.equal(down.size, 0);
});
