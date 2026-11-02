import Foundation

/// A PC as the relay reports it (`presence`).
public struct RemoteHost: Equatable, Identifiable {
    public var id: String { deviceID }
    public var deviceID: String
    public var account: String
    public var name: String
    public var online: Bool
    public var lastSeen: Date?

    public init(deviceID: String, account: String, name: String, online: Bool, lastSeen: Date? = nil) {
        self.deviceID = deviceID; self.account = account; self.name = name; self.online = online; self.lastSeen = lastSeen
    }

    public static func parse(presence: [String: Any]) -> [RemoteHost] {
        (presence["hosts"] as? [[String: Any]] ?? []).compactMap { h in
            guard let id = h["device_id"] as? String, let account = h["account"] as? String else { return nil }
            let seen = (h["last_seen"] as? NSNumber).map { Date(timeIntervalSince1970: $0.doubleValue) }
            return RemoteHost(deviceID: id, account: account, name: h["name"] as? String ?? id,
                              online: h["online"] as? Bool ?? false, lastSeen: seen)
        }
    }
}

// MARK: - one session, as a pure state machine

/// The client side of one remote-desktop session: request → (the PC asks its user) → accept → offer → answer.
/// Every message from the PC must carry a valid signature from that PC's Link key; anything else is ignored.
public final class RemoteSessionMachine {
    public enum State: Equatable { case idle, waiting, negotiating, connected, ended(String) }
    public enum Event: Equatable {
        case accepted(iceServers: [IceServer])
        case offer(sdp: String)
        case candidate(sdp: String, mLineIndex: Int32)
        case ended(reason: String)
    }
    public struct IceServer: Equatable {
        public var urls: [String]
        public var username: String?
        public var credential: String?
        public init(urls: [String], username: String? = nil, credential: String? = nil) {
            self.urls = urls; self.username = username; self.credential = credential
        }
    }

    public let identity: LinkIdentity
    public let hostID: String
    public let hostPublicKey: Data
    public private(set) var sessionID = ""
    public private(set) var state = State.idle
    private let clock: () -> Date

    public init(identity: LinkIdentity, hostPublicKey: Data, clock: @escaping () -> Date = Date.init) {
        self.identity = identity
        self.hostPublicKey = hostPublicKey
        self.hostID = RemoteProtocol.deviceID(hostPublicKey)
        self.clock = clock
    }

    private func signed(_ type: String, _ fields: [String: Any]) throws -> [String: Any] {
        var f = fields
        f["session_id"] = sessionID
        return try RemoteProtocol.sign(RemoteProtocol.routed(type, from: identity.deviceID, to: hostID, f, now: clock()),
                                       identity: identity, peerPublic: hostPublicKey)
    }

    /// The signed `session.request` to send; starts a new session id.
    public func request(name: String, maxWidth: Int, maxHeight: Int, fps: Int = 30) throws -> [String: Any] {
        sessionID = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        state = .waiting
        return try signed("session.request", ["client": String(name.prefix(40)),
                                              "want": ["max_w": maxWidth, "max_h": maxHeight, "fps": fps, "audio": false,
                                                       "codecs": ["H264", "VP8"]]])
    }

    public func answer(sdp: String) throws -> [String: Any] { try signed("sdp.answer", ["sdp": sdp]) }

    public func localCandidate(sdp: String, mLineIndex: Int32) -> [String: Any] {
        RemoteProtocol.routed("ice", from: identity.deviceID, to: hostID,
                              ["session_id": sessionID, "candidate": ["candidate": sdp, "sdpMLineIndex": Int(mLineIndex)]])
    }

    public func end(reason: String) throws -> [String: Any]? {
        guard state != .idle, !isEnded else { return nil }
        state = .ended(reason)
        return try signed("session.end", ["reason": reason])
    }

    public func connected() { if state == .negotiating { state = .connected } }

    public var isEnded: Bool { if case .ended = state { return true } else { return false } }

    /// A routed message from the relay. Returns what the app must do (nothing for anything not ours / not valid).
    public func handle(_ m: [String: Any]) -> [Event] {
        guard let type = m["type"] as? String, m["session_id"] as? String == sessionID, !sessionID.isEmpty,
              m["from"] as? String == hostID, !isEnded else { return [] }
        if RemoteProtocol.signedTypes.contains(type) {
            guard (try? RemoteProtocol.verify(m, identity: identity, peerPublic: hostPublicKey, now: clock())) != nil else {
                return []
            }
        } else if type != "ice" {
            return []
        }
        switch type {
        case "session.accept" where state == .waiting:
            state = .negotiating
            let servers = (m["ice_servers"] as? [[String: Any]] ?? []).map { s -> IceServer in
                let urls = (s["urls"] as? [String]) ?? [(s["urls"] as? String)].compactMap { $0 }
                return IceServer(urls: urls, username: s["username"] as? String, credential: s["credential"] as? String)
            }
            return [.accepted(iceServers: servers)]
        case "sdp.offer" where state == .negotiating:
            return (m["sdp"] as? String).map { [.offer(sdp: $0)] } ?? []
        case "ice" where state == .negotiating || state == .connected:
            guard let c = m["candidate"] as? [String: Any], let sdp = c["candidate"] as? String, !sdp.isEmpty else { return [] }
            return [.candidate(sdp: sdp, mLineIndex: Int32((c["sdpMLineIndex"] as? NSNumber)?.intValue ?? 0))]
        case "session.deny", "session.end":
            let reason = m["reason"] as? String ?? "ended"
            state = .ended(reason)
            return [.ended(reason: reason)]
        default:
            return []
        }
    }
}

// MARK: - the relay connection

/// A text WebSocket. URLSessionRemoteSocket in the app; a scripted one in tests.
public protocol RemoteSocket: AnyObject {
    func send(_ text: String) async throws
    func receive() async throws -> String
    func close()
}

public final class URLSessionRemoteSocket: RemoteSocket {
    private let task: URLSessionWebSocketTask
    public init(url: URL) {
        task = URLSession.shared.webSocketTask(with: url)
        task.maximumMessageSize = 64 * 1024
        task.resume()
    }
    public func send(_ text: String) async throws { try await task.send(.string(text)) }
    public func receive() async throws -> String {
        switch try await task.receive() {
        case .string(let s): return s
        case .data(let d): return String(decoding: d, as: UTF8.self)
        @unknown default: return ""
        }
    }
    public func close() { task.cancel(with: .normalClosure, reason: nil) }
}

/// Logs in to the Halo Relay with this phone's Link key, keeps the connection (reconnecting with backoff) and
/// passes routed messages on. One per app.
public final class RemoteRelayClient {
    public enum Status: Equatable { case offline, connecting, online, reconnecting(seconds: Int), failed(String) }

    public var onStatus: ((Status) -> Void)?
    public var onPresence: (([RemoteHost]) -> Void)?
    public var onMessage: (([String: Any]) -> Void)?
    public private(set) var iceServers: [[String: Any]] = []
    public private(set) var status = Status.offline { didSet { onStatus?(status) } }

    private let identity: LinkIdentity
    private let accounts: [String]
    private let makeSocket: () -> RemoteSocket
    private var socket: RemoteSocket?
    private var loop: Task<Void, Never>?
    private var stopped = false

    public init(identity: LinkIdentity, accounts: [String], makeSocket: @escaping () -> RemoteSocket) {
        self.identity = identity
        self.accounts = accounts
        self.makeSocket = makeSocket
    }

    public convenience init(url: URL, identity: LinkIdentity, accounts: [String]) {
        self.init(identity: identity, accounts: accounts, makeSocket: { URLSessionRemoteSocket(url: url) })
    }

    public func start() {
        stopped = false
        loop?.cancel()
        loop = Task { [weak self] in
            var backoff = 1.0
            while let self = self, !self.stopped, !Task.isCancelled {
                do {
                    try await self.runOnce()
                    backoff = 1
                } catch RemoteProtocol.Failure.badCode {
                    self.status = .failed("login_refused")
                    return
                } catch {
                    // dropped or unreachable: try again
                }
                if self.stopped || Task.isCancelled { break }
                self.status = .reconnecting(seconds: Int(backoff))
                try? await Task.sleep(nanoseconds: UInt64(backoff * 1_000_000_000))
                backoff = min(backoff * 2, 30)
            }
        }
    }

    public func stop() {
        stopped = true
        loop?.cancel()
        socket?.close()
        socket = nil
        status = .offline
    }

    /// One connection: hello → challenge → proof → welcome, then messages until it drops.
    func runOnce() async throws {
        status = .connecting
        let s = makeSocket()
        socket = s
        defer { s.close() }
        try await s.send(try json(["type": "hello", "v": RemoteProtocol.version, "device_id": identity.deviceID,
                                   "role": "client", "pubkey": RemoteProtocol.b64(identity.publicKey),
                                   "name": String(identity.name.prefix(64)), "accounts": accounts]))
        while true {
            let text = try await s.receive()
            guard let data = text.data(using: .utf8), data.count <= 64 * 1024,
                  let m = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let type = m["type"] as? String else { continue }
            switch type {
            case "relay.challenge":
                try await s.send(try json(try RemoteProtocol.relayProof(identity: identity, challenge: m, role: "client")))
            case "welcome":
                iceServers = m["ice_servers"] as? [[String: Any]] ?? []
                status = .online
            case "presence":
                onPresence?(RemoteHost.parse(presence: m))
            case "error" where status != .online && (m["reason"] as? String) == "login_failed":
                throw RemoteProtocol.Failure.badCode
            default:
                onMessage?(m)
            }
        }
    }

    public func send(_ message: [String: Any]) async throws {
        guard let s = socket, status == .online else { throw RemoteProtocol.Failure.missing("relay") }
        try await s.send(try json(message))
    }

    func json(_ m: [String: Any]) throws -> String {
        String(decoding: try JSONSerialization.data(withJSONObject: m), as: UTF8.self)
    }
}
