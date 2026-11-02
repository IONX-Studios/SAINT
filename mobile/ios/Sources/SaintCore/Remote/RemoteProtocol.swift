import Foundation

/// Halo Remote protocol v1 (docs/REMOTE_DESKTOP.md; modules/remote_desktop/protocol.py on the PC,
/// web/remote/js/protocol.js in the browser). The three implementations are checked against the same
/// known answers: Tests/SaintCoreTests/Resources/remote_vectors.json.
///
/// Link keys are X25519, which can't sign, so a message "signature" is an HMAC-SHA256 under a key only the two
/// paired devices can derive: HKDF(X25519(mine, theirs), "halo-remote/v1", "session-mac" ‖ min(pubs) ‖ max(pubs)).
public enum RemoteProtocol {
    public static let version = 1
    public static let maxSkew: TimeInterval = 300
    static let salt = Data("halo-remote/v1".utf8)

    public enum Failure: Error, Equatable {
        case floatInSignedMessage, nonASCIIKey, unsupportedValue
        case badBase64, notSignedType, wrongSender, wrongRecipient, badSignature, stale, badCode, missing(String)
    }

    public static let signedTypes: Set<String> = ["session.request", "session.accept", "session.deny", "sdp.offer",
                                                  "sdp.answer", "session.end"]

    // MARK: identifiers

    public static func deviceID(_ publicKey: Data) -> String { Pairing.deviceID(forPublicKey: publicKey) }
    public static func accountID(_ hostPublicKey: Data) -> String { SaintCrypto.sha256(hostPublicKey).hex }

    // MARK: canonical JSON

    /// Sorted keys, no whitespace, UTF-8, the escapes JSON.stringify / Python's json make. Integers, strings,
    /// booleans, null, arrays and objects only — no floats, so every language encodes a message the same way.
    public static func canonical(_ value: Any?) throws -> Data {
        var out = ""
        try encode(value, into: &out)
        return Data(out.utf8)
    }

    /// JSONSerialization gives NSNumber for numbers and booleans, and Swift's Int/Bool/Double bridge to it:
    /// its Objective-C type tells them apart ("c" bool, "d"/"f" floating point, the rest integers).
    static func numberKind(_ n: NSNumber) -> Character {
        let t = String(cString: n.objCType)
        if t == "c" || t == "B" { return "b" }
        if t == "d" || t == "f" { return "f" }
        return "i"
    }

    private static func encode(_ value: Any?, into out: inout String) throws {
        switch value {
        case nil, is NSNull:
            out += "null"
        case let n as NSNumber:
            switch numberKind(n) {
            case "b": out += n.boolValue ? "true" : "false"
            case "f": throw Failure.floatInSignedMessage
            default:
                guard abs(n.int64Value) <= 9_007_199_254_740_991 else { throw Failure.unsupportedValue }
                out += String(n.int64Value)
            }
        case let s as String:
            out += quote(s)
        case let a as [Any?]:
            out += "["
            for (i, v) in a.enumerated() {
                if i > 0 { out += "," }
                try encode(v, into: &out)
            }
            out += "]"
        case let a as [Any]:
            try encode(a.map { Optional($0) }, into: &out)
        case let o as [String: Any?]:
            for k in o.keys where !k.unicodeScalars.allSatisfy({ $0.isASCII }) { throw Failure.nonASCIIKey }
            let keys = o.keys.sorted { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
            out += "{"
            for (i, k) in keys.enumerated() {
                if i > 0 { out += "," }
                out += quote(k) + ":"
                try encode(o[k] ?? nil, into: &out)
            }
            out += "}"
        case let o as [String: Any]:
            try encode(o.mapValues { Optional($0) }, into: &out)
        default:
            throw Failure.unsupportedValue
        }
    }

    static func quote(_ s: String) -> String {
        var out = "\""
        for u in s.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{08}": out += "\\b"
            case "\u{0C}": out += "\\f"
            default:
                if u.value < 0x20 {
                    out += String(format: "\\u%04x", u.value)
                } else {
                    out.unicodeScalars.append(u)
                }
            }
        }
        return out + "\""
    }

    // MARK: base64

    public static func b64(_ data: Data) -> String { data.base64EncodedString() }

    public static func unb64(_ text: Any?, size: Int? = nil) throws -> Data {
        guard let s = text as? String, let d = Data(base64Encoded: s), size == nil || d.count == size else {
            throw Failure.badBase64
        }
        return d
    }

    // MARK: device-to-device MACs

    public static func sessionKey(privateKey: Data, publicKey: Data, peerPublic: Data) throws -> Data {
        let shared = try SaintCrypto.sharedSecret(privateKey: privateKey, peerPublic: peerPublic)
        let (low, high) = publicKey.lexicographicallyPrecedes(peerPublic) ? (publicKey, peerPublic) : (peerPublic, publicKey)
        return SaintCrypto.hkdf(ikm: shared, salt: salt, info: Data("session-mac".utf8) + low + high, length: 32)
    }

    static func mac(key: Data, label: String, _ object: [String: Any]) throws -> String {
        let input = Data("halo-remote/v1 \(label)\u{0}".utf8) + (try canonical(object))
        return b64(SaintCrypto.hmacSHA256(key: key, data: input))
    }

    /// `message` with `from` (if missing) and `sig` filled in.
    public static func sign(_ message: [String: Any], identity: LinkIdentity, peerPublic: Data) throws -> [String: Any] {
        var body = message
        body["sig"] = nil
        if body["from"] == nil { body["from"] = identity.deviceID }
        let key = try sessionKey(privateKey: identity.privateKey, publicKey: identity.publicKey, peerPublic: peerPublic)
        body["sig"] = try mac(key: key, label: "sig", body)
        return body
    }

    @discardableResult
    public static func verify(_ message: [String: Any], identity: LinkIdentity, peerPublic: Data,
                              now: Date = Date()) throws -> [String: Any] {
        guard let type = message["type"] as? String, signedTypes.contains(type) else { throw Failure.notSignedType }
        guard let sig = message["sig"] as? String else { throw Failure.missing("sig") }
        guard message["from"] as? String == deviceID(peerPublic) else { throw Failure.wrongSender }
        guard message["to"] as? String == identity.deviceID else { throw Failure.wrongRecipient }
        var body = message
        body["sig"] = nil
        let key = try sessionKey(privateKey: identity.privateKey, publicKey: identity.publicKey, peerPublic: peerPublic)
        guard constantTimeEqual(try mac(key: key, label: "sig", body), sig) else { throw Failure.badSignature }
        try checkFresh(message, now: now)
        return message
    }

    static func checkFresh(_ message: [String: Any], now: Date) throws {
        guard let ts = (message["ts"] as? NSNumber)?.doubleValue, abs(now.timeIntervalSince1970 - ts) <= maxSkew else {
            throw Failure.stale
        }
    }

    static func constantTimeEqual(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard x.count == y.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<x.count { diff |= x[i] ^ y[i] }
        return diff == 0
    }

    /// A routed message skeleton; `ts` for every type that carries one.
    public static func routed(_ type: String, from: String, to: String, _ fields: [String: Any] = [:],
                              now: Date = Date()) -> [String: Any] {
        var m: [String: Any] = ["type": type, "from": from, "to": to]
        if !["ice", "pair.deny"].contains(type) { m["ts"] = Int(now.timeIntervalSince1970) }
        for (k, v) in fields { m[k] = v }
        return m
    }

    // MARK: relay login

    public static func relayProof(identity: LinkIdentity, challenge: [String: Any], role: String) throws -> [String: Any] {
        let eph = try unb64(challenge["eph"], size: 32), nonce = try unb64(challenge["nonce"], size: 32)
        let shared = try SaintCrypto.sharedSecret(privateKey: identity.privateKey, peerPublic: eph)
        let key = SaintCrypto.hkdf(ikm: shared, salt: salt, info: Data("relay-login".utf8), length: 32)
        let input = nonce + Data(identity.deviceID.utf8) + Data([0]) + Data(role.utf8)
        return ["type": "relay.proof", "sig": b64(SaintCrypto.hmacSHA256(key: key, data: input))]
    }

    // MARK: pairing through the relay

    public static func pairMAC(_ message: [String: Any], psk: Data) throws -> [String: Any] {
        var body = message
        body["mac"] = nil
        let key = SaintCrypto.hkdf(ikm: psk, salt: salt, info: Data("pair".utf8), length: 32)
        body["mac"] = try mac(key: key, label: "pair", body)
        return body
    }

    public static func checkPairMAC(_ message: [String: Any], psk: Data, now: Date = Date()) throws {
        var body = message
        body["mac"] = nil
        let key = SaintCrypto.hkdf(ikm: psk, salt: salt, info: Data("pair".utf8), length: 32)
        guard let mac = message["mac"] as? String, constantTimeEqual(try self.mac(key: key, label: "pair", body), mac) else {
            throw Failure.badCode
        }
        try checkFresh(message, now: now)
    }
}
