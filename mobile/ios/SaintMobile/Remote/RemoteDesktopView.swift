import SwiftUI
import UIKit
import UniformTypeIdentifiers
import WebRTC
import SaintCore

/// Devices → Halo Remote: the relay, your PCs (online or not), Connect.
struct RemoteDesktopView: View {
    @EnvironmentObject var settings: AppSettings
    @EnvironmentObject var remote: RemoteDesktopModel

    var body: some View {
        List {
            Section {
                TextField("wss://relay.example.com/ws", text: $settings.remoteRelayURL)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .onSubmit { remote.start() }
                HStack {
                    Text("Relay").foregroundStyle(Theme.muted)
                    Spacer()
                    Text(statusText).foregroundStyle(statusColor)
                }
            } header: { Text("Relay") } footer: {
                Text("Filled in from your PC when it's reachable over SAINT Link (or see `halo-remote status` on the PC). "
                     + "Your PC asks before each connection unless you set this iPhone to \"allow\" for remote desktop "
                     + "in SAINT → Devices.")
            }
            .listRowBackground(Theme.surface)

            Section("Your PCs") {
                if remote.pcs.isEmpty {
                    Text("Pair your PC first (Devices → Pair), then come back here.").foregroundStyle(Theme.muted)
                }
                ForEach(remote.pcs) { pc in
                    HStack {
                        Circle().fill(pc.online ? Theme.success : Theme.faint).frame(width: 9, height: 9)
                        VStack(alignment: .leading) {
                            Text(pc.presence?.name ?? pc.peer.name).foregroundStyle(Theme.text)
                            Text(pc.presence == nil ? "not on the relay" : (pc.online ? "online" : "offline"))
                                .font(.caption).foregroundStyle(Theme.muted)
                        }
                        Spacer()
                        Button("Connect") { remote.connect(pc) }
                            .buttonStyle(.borderedProminent).tint(Theme.accent)
                            .disabled(!pc.online)
                    }
                }
            }
            .listRowBackground(Theme.surface)
        }
        .scrollContentBackground(.hidden)
        .background(Theme.bg)
        .navigationTitle("Halo Remote")
        .onAppear { remote.start() }
        .fullScreenCover(item: $remote.session) { session in
            RemoteSessionView(session: session) { remote.endSession() }
        }
    }

    private var statusText: String {
        switch remote.status {
        case .offline: return settings.remoteRelayURL.isEmpty ? "not set" : "offline"
        case .connecting: return "connecting…"
        case .online: return "online"
        case .reconnecting(let s): return "retrying in \(s)s"
        case .failed(let why): return why
        }
    }
    private var statusColor: Color { remote.status == .online ? Theme.success : Theme.muted }
}

/// The live screen: video, touch-to-mouse, keyboard, clipboard, files, stats.
struct RemoteSessionView: View {
    @ObservedObject var session: RemoteSessionModel
    let close: () -> Void
    @State private var keyboard = false
    @State private var importing = false
    @State private var resetZoom = 0

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                Color.black
                RemoteScreen(session: session, keyboard: $keyboard, resetZoom: resetZoom)
                if let text = overlayText {
                    Text(text)
                        .font(.callout).foregroundStyle(Theme.text).multilineTextAlignment(.center)
                        .padding(12).background(Theme.raised.opacity(0.92), in: RoundedRectangle(cornerRadius: 10))
                        .padding()
                        .allowsHitTesting(false)
                }
            }
            toolbar
        }
        .background(Color.black.ignoresSafeArea())
        .statusBarHidden()
        .fileImporter(isPresented: $importing, allowedContentTypes: [.item]) { result in
            if case .success(let url) = result {
                Task {
                    let ok = await session.sendFile(url)
                    session.status = ok ? "\(url.lastPathComponent) is in Downloads/Halo Remote on the PC"
                                        : "\(url.lastPathComponent) didn't arrive intact"
                    try? await Task.sleep(nanoseconds: 2_500_000_000)
                    session.status = ""
                }
            }
        }
    }

    private var overlayText: String? {
        if let f = session.finished, f != "closed" { return f }
        return session.status.isEmpty ? nil : session.status
    }

    private var toolbar: some View {
        HStack(spacing: 14) {
            Button { keyboard.toggle() } label: { Image(systemName: "keyboard") }
            Menu {
                Button("Ctrl+Alt+Del") { session.send(RemoteInput.combo(["Control", "Alt", "Delete"])) }
                Button("Super") { session.send(RemoteInput.combo(["Super"])) }
                Button("Esc") { session.send(RemoteInput.combo(["Escape"])) }
                Button("Tab") { session.send(RemoteInput.combo(["Tab"])) }
                Button("Alt+Tab") { session.send(RemoteInput.combo(["Alt", "Tab"])) }
                Button("Ctrl+C") { session.send(RemoteInput.combo(["Control", "C"])) }
                Button("Ctrl+V") { session.send(RemoteInput.combo(["Control", "V"])) }
                ForEach(["Up", "Down", "Left", "Right"], id: \.self) { k in
                    Button(k) { session.send(RemoteInput.combo([k])) }
                }
            } label: { Image(systemName: "command") }
            Button { session.pasteFromPhone() } label: { Image(systemName: "doc.on.clipboard") }
            Button { session.copyFromPC() } label: { Image(systemName: "square.and.arrow.down.on.square") }
                .disabled(session.remoteClipboard == nil)
            Button { importing = true } label: { Image(systemName: "square.and.arrow.up") }
                .disabled(!session.connected)
            Button { resetZoom += 1 } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }
            Text(session.stats).font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted).lineLimit(1)
            Spacer(minLength: 0)
            Button("Done") { close() }.foregroundStyle(Theme.danger)
        }
        .font(.title3)
        .tint(Theme.text)
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(Theme.surface)
    }
}

/// UIKit host for the video (RTCMTLVideoView), multi-touch and the system keyboard.
struct RemoteScreen: UIViewRepresentable {
    @ObservedObject var session: RemoteSessionModel
    @Binding var keyboard: Bool
    var resetZoom: Int

    func makeUIView(context: Context) -> RemoteScreenView {
        let v = RemoteScreenView()
        v.onSend = { [weak session = session] msgs in session?.send(msgs) }
        return v
    }

    func updateUIView(_ v: RemoteScreenView, context: Context) {
        v.attach(track: session.videoTrack)
        v.setKeyboard(keyboard)
        if v.resetCount != resetZoom { v.resetCount = resetZoom; v.resetView() }
    }
}

final class RemoteScreenView: UIView, RTCVideoViewDelegate {
    var onSend: (([[String: Any]]) -> Void)?
    var resetCount = 0
    private let video = RTCMTLVideoView()
    private let keys = KeyInputView()
    private let gestures = TouchGestures()
    private weak var track: RTCVideoTrack?
    private var videoSize = CGSize.zero
    private var ticker: Timer?
    private var active: [UITouch: Int] = [:]
    private var nextID = 0

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .black
        isMultipleTouchEnabled = true
        video.videoContentMode = .scaleAspectFit
        video.delegate = self
        video.isUserInteractionEnabled = false
        addSubview(video)
        addSubview(keys)
        keys.onSend = { [weak self] in self?.onSend?($0) }
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    override func layoutSubviews() {
        super.layoutSubviews()
        if video.transform == .identity { video.frame = bounds }
    }

    func attach(track newTrack: RTCVideoTrack?) {
        guard newTrack !== track else { return }
        track?.remove(video)
        track = newTrack
        newTrack?.add(video)
    }

    func videoView(_ videoView: RTCVideoRenderer, didChangeVideoSize size: CGSize) { videoSize = size }

    func setKeyboard(_ on: Bool) {
        if on && !keys.isFirstResponder { keys.becomeFirstResponder() }
        if !on && keys.isFirstResponder { keys.resignFirstResponder() }
    }

    func resetView() { apply(gestures.resetView()) }

    // MARK: touches -> TouchGestures (SaintCore) -> control messages

    private func current(_ event: UIEvent?) -> [RemoteTouch] {
        let frame = video.frame                        // includes the zoom transform
        return active.sorted { $0.value < $1.value }.map { touch, id in
            let p = touch.location(in: self)
            let n = RemoteInput.normalized(x: Double(p.x - frame.minX), y: Double(p.y - frame.minY),
                                           viewWidth: Double(frame.width), viewHeight: Double(frame.height),
                                           videoWidth: Double(videoSize.width), videoHeight: Double(videoSize.height))
            return RemoteTouch(id: id, x: n?.x ?? 0, y: n?.y ?? 0, px: Double(p.x), py: Double(p.y))
        }
    }

    private func apply(_ outputs: [GestureOutput]) {
        var msgs: [[String: Any]] = []
        for o in outputs {
            switch o {
            case .send(let m): msgs.append(m)
            case .view(let zoom, let panX, let panY):
                video.transform = CGAffineTransform(translationX: panX, y: panY).scaledBy(x: zoom, y: zoom)
            }
        }
        if !msgs.isEmpty { onSend?(msgs) }
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches { active[t] = nextID; nextID += 1 }
        apply(gestures.feed(.start, current(event), at: event?.timestamp ?? 0))
        if ticker == nil {
            ticker = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                self.apply(self.gestures.tick(at: ProcessInfo.processInfo.systemUptime))
            }
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        apply(gestures.feed(.move, current(event), at: event?.timestamp ?? 0))
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches { active[t] = nil }
        apply(gestures.feed(.end, current(event), at: event?.timestamp ?? 0))
        if active.isEmpty { ticker?.invalidate(); ticker = nil }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches { active[t] = nil }
        apply(gestures.feed(.cancel, current(event), at: event?.timestamp ?? 0))
        if active.isEmpty { ticker?.invalidate(); ticker = nil }
    }
}

/// An invisible text field stand-in: what the iPhone keyboard types goes to the PC as text.
final class KeyInputView: UIView, UIKeyInput {
    var onSend: (([[String: Any]]) -> Void)?
    var autocorrectionType: UITextAutocorrectionType = .no
    var autocapitalizationType: UITextAutocapitalizationType = .none
    var spellCheckingType: UITextSpellCheckingType = .no
    var smartQuotesType: UITextSmartQuotesType = .no
    var smartDashesType: UITextSmartDashesType = .no
    var keyboardType: UIKeyboardType = .asciiCapable

    override var canBecomeFirstResponder: Bool { true }
    var hasText: Bool { true }                         // so Backspace always reaches deleteBackward
    func insertText(_ text: String) { onSend?(RemoteInput.text(text)) }
    func deleteBackward() { onSend?(RemoteInput.backspace) }
}
