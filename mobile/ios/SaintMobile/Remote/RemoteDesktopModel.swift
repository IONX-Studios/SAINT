import Foundation
import UIKit
import WebRTC
import SaintCore

/// Halo Remote on the phone: the relay connection and the list of your PCs. The phone uses the same Link key it
/// paired with (no second pairing): the PC lets its own devices ask for remote desktop, and asks you on screen
/// unless you set this phone to "allow" in SAINT → Devices on the PC.
@MainActor
final class RemoteDesktopModel: ObservableObject {
    struct PC: Identifiable, Equatable {
        var id: String { peer.id }
        var peer: LinkPeer
        var presence: RemoteHost?
        var online: Bool { presence?.online ?? false }
    }

    @Published private(set) var pcs: [PC] = []
    @Published private(set) var status: RemoteRelayClient.Status = .offline
    @Published var session: RemoteSessionModel?

    private let link: LinkManager
    private let settings: AppSettings
    private var relay: RemoteRelayClient?
    private var relayURL = ""

    init(link: LinkManager, settings: AppSettings) {
        self.link = link
        self.settings = settings
    }

    /// Your own paired computers (phones and friends' PCs can't be controlled from here).
    private var pcPeers: [LinkPeer] {
        link.peerStore.all().filter { $0.isOwn && !["ios", "android"].contains($0.platform) }
    }

    func start() {
        let url = settings.remoteRelayURL.trimmingCharacters(in: .whitespacesAndNewlines)
        let peers = pcPeers
        pcs = peers.map { p in PC(peer: p, presence: pcs.first(where: { $0.id == p.id })?.presence) }
        guard let u = URL(string: url), ["wss", "ws"].contains(u.scheme ?? ""), !peers.isEmpty else {
            stop()
            return
        }
        if relay != nil && url == relayURL { return }
        stop()
        relayURL = url
        let client = RemoteRelayClient(url: u, identity: link.identity,
                                       accounts: peers.map { RemoteProtocol.accountID($0.key) })
        client.onStatus = { [weak self] s in Task { @MainActor in self?.status = s } }
        client.onPresence = { [weak self] hosts in Task { @MainActor in self?.apply(hosts) } }
        client.onMessage = { [weak self] m in Task { @MainActor in self?.session?.handle(m) } }
        relay = client
        client.start()
    }

    func stop() {
        relay?.stop()
        relay = nil
        status = .offline
    }

    private func apply(_ hosts: [RemoteHost]) {
        pcs = pcs.map { pc in
            var p = pc
            p.presence = hosts.first(where: { $0.deviceID == pc.id })
            return p
        }
    }

    func connect(_ pc: PC) {
        guard let relay = relay else { return }
        session?.stop()
        session = RemoteSessionModel(relay: relay, identity: link.identity, host: pc.peer)
        session?.start()
    }

    func endSession() {
        session?.stop()
        session = nil
    }
}

/// One live session: signaling (RemoteSessionMachine, tested in SaintCore), the WebRTC connection, input,
/// clipboard, files, stats and automatic reconnect after a dropped connection.
@MainActor
final class RemoteSessionModel: ObservableObject, Identifiable {
    let id = UUID()
    let hostName: String
    @Published var status = "Asking your PC…"
    @Published var connected = false
    @Published var videoTrack: RTCVideoTrack?
    @Published var stats = ""
    @Published var remoteClipboard: String?
    @Published var finished: String?

    private let relay: RemoteRelayClient
    private let identity: LinkIdentity
    private let host: LinkPeer
    private var machine: RemoteSessionMachine
    private var peer: WebRTCPeer?
    private var retries = 0
    private var statsTimer: Timer?

    init(relay: RemoteRelayClient, identity: LinkIdentity, host: LinkPeer) {
        self.relay = relay
        self.identity = identity
        self.host = host
        self.hostName = host.name
        machine = RemoteSessionMachine(identity: identity, hostPublicKey: host.key)
    }

    func start() {
        machine = RemoteSessionMachine(identity: identity, hostPublicKey: host.key)
        status = retries == 0 ? "Waiting for \(hostName) to allow this iPhone…" : "Reconnecting (\(retries)/5)…"
        let screen = UIScreen.main.nativeBounds.size
        let req: [String: Any]
        do {
            req = try machine.request(name: identity.name, maxWidth: Int(max(screen.width, screen.height)),
                                      maxHeight: Int(min(screen.width, screen.height)))
        } catch {
            finished = "Couldn't sign the request."
            return
        }
        Task {
            do { try await relay.send(req) } catch { self.ended("offline") }
        }
    }

    func handle(_ m: [String: Any]) {
        for event in machine.handle(m) {
            switch event {
            case .accepted(let servers):
                status = "Connecting…"
                startPeer(servers.isEmpty ? relayServers() : servers)
            case .offer(let sdp):
                Task {
                    do {
                        guard let answer = try await peer?.answer(offer: sdp) else { return }
                        try await relay.send(try machine.answer(sdp: answer))
                    } catch {
                        self.ended("connection_lost")
                    }
                }
            case .candidate(let sdp, let index):
                peer?.addCandidate(sdp: sdp, mLineIndex: index)
            case .ended(let reason):
                ended(reason)
            }
        }
    }

    private func relayServers() -> [RemoteSessionMachine.IceServer] {
        relay.iceServers.map { s in
            RemoteSessionMachine.IceServer(urls: (s["urls"] as? [String]) ?? [], username: s["username"] as? String,
                                           credential: s["credential"] as? String)
        }
    }

    private func startPeer(_ servers: [RemoteSessionMachine.IceServer]) {
        let p = WebRTCPeer(iceServers: servers)
        p.onVideoTrack = { [weak self] track in self?.videoTrack = track }
        p.onCandidate = { [weak self] sdp, index in
            guard let self = self else { return }
            let msg = self.machine.localCandidate(sdp: sdp, mLineIndex: index)
            Task { try? await self.relay.send(msg) }
        }
        p.onState = { [weak self] state in
            guard let self = self else { return }
            switch state {
            case .connected:
                self.machine.connected()
                self.connected = true
                self.retries = 0
                self.status = ""
                self.startStats()
            case .failed:
                self.ended("connection_lost")
            case .disconnected:
                self.status = "Connection unstable…"
            default:
                break
            }
        }
        p.onControl = { [weak self] m in
            guard let self = self else { return }
            if m["type"] as? String == "stats" {
                self.hostKbps = (m["kbps"] as? NSNumber)?.intValue ?? 0
            } else if m["type"] as? String == "clipboard", let text = m["data"] as? String {
                self.remoteClipboard = text
            }
        }
        peer = p
    }

    private var hostKbps = 0

    private func startStats() {
        statsTimer?.invalidate()
        statsTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self = self, let p = self.peer else { return }
                let s = await p.stats()
                self.stats = "\(s.relayed ? "via TURN" : "direct") · \(s.rtt) ms · \(s.fps) fps · \(self.hostKbps) kbps"
            }
        }
    }

    private func ended(_ reason: String) {
        connected = false
        statsTimer?.invalidate()
        peer?.close()
        peer = nil
        videoTrack = nil
        if reason == "connection_lost" && retries < 5 {
            retries += 1
            status = "The connection dropped. Reconnecting (\(retries)/5)…"
            let wait = min(pow(2.0, Double(retries)), 15)
            DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self] in
                guard let self = self, self.finished == nil else { return }
                self.start()
            }
            return
        }
        finished = Self.explain(reason)
    }

    static func explain(_ reason: String) -> String {
        switch reason {
        case "declined": return "Your PC said no."
        case "no_answer": return "Nobody answered on the PC."
        case "busy": return "Someone else is connected to that PC."
        case "not_paired", "revoked": return "This iPhone isn't allowed on that PC any more. Pair it again."
        case "not_allowed": return "Remote desktop is off for this iPhone (SAINT → Devices on the PC)."
        case "connection_lost": return "The connection dropped."
        case "offline": return "Not connected to the relay."
        case "portal_start_refused": return "The PC refused screen sharing — approve Halo Remote once at the PC."
        default: return "Session ended (\(reason))."
        }
    }

    // MARK: input

    func send(_ message: [String: Any]) { peer?.sendControl(message) }
    func send(_ messages: [[String: Any]]) { messages.forEach { send($0) } }

    func pasteFromPhone() {
        if let text = UIPasteboard.general.string, !text.isEmpty {
            send(["type": "clipboard", "mime": "text/plain", "data": text])
        }
    }

    func copyFromPC() {
        if let text = remoteClipboard { UIPasteboard.general.string = text }
    }

    /// Send a file over the "files" channel (64 KiB chunks); the PC checks the SHA-256.
    func sendFile(_ url: URL) async -> Bool {
        guard let p = peer else { return false }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        guard let data = try? Data(contentsOf: url), data.count <= 1 << 30 else { return false }
        let fileID = UUID().uuidString
        let reply = AsyncStream<[String: Any]> { cont in p.onFilesMessage = { cont.yield($0) } }
        var it = reply.makeAsyncIterator()
        guard p.sendFiles(text: ["type": "file.offer", "id": fileID, "name": url.lastPathComponent, "size": data.count,
                                 "sha256": SaintCrypto.sha256(data).hex]) else { return false }
        guard let first = await it.next(), first["type"] as? String == "file.accept" else { return false }
        var offset = 0
        while offset < data.count {
            while p.filesBuffered > 1 << 20 { try? await Task.sleep(nanoseconds: 20_000_000) }
            let end = min(offset + 65536, data.count)
            guard p.sendFiles(binary: data.subdata(in: offset..<end)) else { return false }
            offset = end
            status = "Sending \(url.lastPathComponent): \(offset * 100 / max(data.count, 1))%"
        }
        let done = await it.next()
        status = ""
        return done?["ok"] as? Bool == true
    }

    func stop() {
        if let m = try? machine.end(reason: "closed") { Task { try? await relay.send(m) } }
        finished = finished ?? "closed"
        statsTimer?.invalidate()
        peer?.close()
        peer = nil
    }
}
