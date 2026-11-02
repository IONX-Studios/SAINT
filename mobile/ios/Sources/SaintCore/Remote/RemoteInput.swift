import Foundation

/// Touch, keyboard and pointer input for Halo Remote, as "control" data-channel messages. The same rules as the
/// browser client (web/remote/js/input.js): tap = left click there, long press or two-finger tap = right click,
/// drag = left-button drag, two-finger pan = scroll (or move the zoomed view), pinch = zoom the view on the phone.
public enum RemoteInput {
    /// Linux evdev codes for the keys the iPhone keyboard can't type as text.
    public static let keys: [String: Int] = [
        "Escape": 1, "Backspace": 14, "Tab": 15, "Enter": 28, "Control": 29, "Shift": 42, "Alt": 56, "Space": 57,
        "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67, "F10": 68,
        "F11": 87, "F12": 88, "Home": 102, "Up": 103, "PageUp": 104, "Left": 105, "Right": 106, "End": 107,
        "Down": 108, "PageDown": 109, "Insert": 110, "Delete": 111, "Super": 125,
        "C": 46, "V": 47, "X": 45, "Z": 44, "A": 30,
    ]

    /// Press keys in order and release them in reverse ("Control+Alt+Delete").
    public static func combo(_ names: [String]) -> [[String: Any]] {
        let codes = names.compactMap { keys[$0] }
        return codes.map { ["type": "key", "code": $0, "down": true] } +
            codes.reversed().map { ["type": "key", "code": $0, "down": false] }
    }

    public static func text(_ s: String) -> [[String: Any]] {
        if s == "\n" { return combo(["Enter"]) }
        return s.isEmpty ? [] : [["type": "text", "utf8": s]]
    }

    public static var backspace: [[String: Any]] { combo(["Backspace"]) }

    /// A point in the view -> 0..1 of the picture, which is shown aspect-fit (bars around it).
    public static func normalized(x: Double, y: Double, viewWidth: Double, viewHeight: Double,
                                  videoWidth: Double, videoHeight: Double) -> (x: Double, y: Double)? {
        guard videoWidth > 0, videoHeight > 0, viewWidth > 0, viewHeight > 0 else { return nil }
        let scale = min(viewWidth / videoWidth, viewHeight / videoHeight)
        let w = videoWidth * scale, h = videoHeight * scale
        let left = (viewWidth - w) / 2, top = (viewHeight - h) / 2
        func clamp(_ v: Double) -> Double { min(max(v, 0), 1) }
        return (clamp((x - left) / w), clamp((y - top) / h))
    }
}

/// One finger on the screen. `x`/`y`: 0..1 of the picture (for the PC); `px`/`py`: points on the screen.
public struct RemoteTouch: Equatable {
    public var id: Int
    public var x: Double, y: Double, px: Double, py: Double
    public init(id: Int, x: Double, y: Double, px: Double, py: Double) {
        self.id = id; self.x = x; self.y = y; self.px = px; self.py = py
    }
}

public enum GestureOutput {
    case send([String: Any])
    case view(zoom: Double, panX: Double, panY: Double)
}

public final class TouchGestures {
    public enum Kind { case start, move, end, cancel }
    public static let tapSlop = 10.0, longPress: TimeInterval = 0.55, tapTime: TimeInterval = 0.3

    public private(set) var zoom = 1.0, panX = 0.0, panY = 0.0
    private enum Mode { case idle, one, drag, two, done }
    private var mode = Mode.idle
    private var start: RemoteTouch?
    private var startT: TimeInterval = 0
    private var maxTouches = 0
    private var pressed = false
    private var pinch: (d: Double, cx: Double, cy: Double, zoom: Double, moved: Bool, zooming: Bool)?

    public init() {}

    private func reset() { mode = .idle; start = nil; maxTouches = 0; pressed = false; pinch = nil }
    private func move(_ t: RemoteTouch) -> GestureOutput { .send(["type": "pointer.move", "x": t.x, "y": t.y]) }
    private func button(_ b: Int, _ down: Bool) -> GestureOutput { .send(["type": "pointer.button", "button": b, "down": down]) }
    private var view: GestureOutput { .view(zoom: zoom, panX: panX, panY: panY) }

    public func feed(_ kind: Kind, _ touches: [RemoteTouch], at t: TimeInterval) -> [GestureOutput] {
        var out: [GestureOutput] = []
        let n = touches.count
        switch kind {
        case .start:
            maxTouches = max(maxTouches, n)
            if n == 1 && mode == .idle {
                mode = .one; start = touches[0]; startT = t
            } else if n == 2 && (mode == .one || mode == .idle) {
                if mode == .idle { startT = t }
                mode = .two
                let a = touches[0], b = touches[1]
                pinch = (hypot(a.px - b.px, a.py - b.py), (a.px + b.px) / 2, (a.py + b.py) / 2, zoom, false, false)
            }
        case .move:
            if (mode == .one || mode == .drag) && n == 1, let s = start {
                let p = touches[0]
                if mode == .one && hypot(p.px - s.px, p.py - s.py) > Self.tapSlop {
                    mode = .drag
                    pressed = true
                    out += [move(s), button(1, true)]
                }
                if mode == .drag { out.append(move(p)) }
            } else if mode == .two && n == 2, var pz = pinch {
                let a = touches[0], b = touches[1]
                let d = hypot(a.px - b.px, a.py - b.py)
                let cx = (a.px + b.px) / 2, cy = (a.py + b.py) / 2
                let dx = cx - pz.cx, dy = cy - pz.cy
                if abs(d - pz.d) > 24 || pz.zooming {
                    pz.zooming = true
                    zoom = min(max(pz.zoom * (d / pz.d), 1), 4)
                    if zoom == 1 { panX = 0; panY = 0 }
                    out.append(view)
                } else if hypot(dx, dy) > Self.tapSlop || pz.moved {
                    pz.moved = true
                    if zoom > 1 {
                        panX += dx; panY += dy
                        out.append(view)
                    } else {
                        out.append(.send(["type": "pointer.scroll", "dx": Int((-dx * 2).rounded()),
                                          "dy": Int((-dy * 2).rounded())]))
                    }
                }
                if pz.moved || pz.zooming { pz.cx = cx; pz.cy = cy }
                pinch = pz
            }
        case .cancel:
            if pressed { out.append(button(1, false)) }
            reset()
        case .end:
            if n > 0 { return out }
            let quick = t - startT < Self.tapTime
            if mode == .one && quick, let s = start {
                out += [move(s), button(1, true), button(1, false)]
            } else if mode == .drag && pressed {
                out.append(button(1, false))
            } else if mode == .two, maxTouches == 2, let pz = pinch, !pz.moved, !pz.zooming, quick {
                out += [button(3, true), button(3, false)]
            }
            reset()
        }
        return out
    }

    /// Call while a finger is down (every ~0.1 s): turns a still finger into a right click.
    public func tick(at t: TimeInterval) -> [GestureOutput] {
        guard mode == .one, t - startT >= Self.longPress, let s = start else { return [] }
        mode = .done
        return [move(s), button(3, true), button(3, false)]
    }

    public func resetView() -> [GestureOutput] { zoom = 1; panX = 0; panY = 0; return [view] }
}
