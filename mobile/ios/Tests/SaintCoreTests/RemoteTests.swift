import XCTest
@testable import SaintCore

/// Halo Remote protocol v1 against remote_vectors.json — the same known answers the PC (tests/test_remote_protocol.py)
/// and the browser client (web/remote/test) check.
final class RemoteProtocolTests: XCTestCase {
    var v: [String: Any] = [:]
    var ts: Date = Date()

    override func setUpWithError() throws {
        v = try TestData.object("remote_vectors")
        ts = Date(timeIntervalSince1970: (v["ts"] as! NSNumber).doubleValue)
    }

    func identity(_ name: String) throws -> LinkIdentity {
        let k = try XCTUnwrap((v["keys"] as? [String: Any])?[name] as? [String: String])
        let priv = hexData(k["private"]!)
        return LinkIdentity(privateKey: priv, publicKey: try SaintCrypto.publicKey(forPrivate: priv), name: name)
    }

    func testKeysIDsAccountAndSessionKey() throws {
        let keys = try XCTUnwrap(v["keys"] as? [String: [String: String]])
        for (name, k) in keys {
            let id = try identity(name)
            XCTAssertEqual(RemoteProtocol.b64(id.publicKey), k["public"])
            XCTAssertEqual(id.deviceID, k["device_id"])
        }
        let host = try identity("host"), client = try identity("client")
        XCTAssertEqual(RemoteProtocol.accountID(host.publicKey), v["account_id"] as? String)
        let a = try RemoteProtocol.sessionKey(privateKey: client.privateKey, publicKey: client.publicKey, peerPublic: host.publicKey)
        let b = try RemoteProtocol.sessionKey(privateKey: host.privateKey, publicKey: host.publicKey, peerPublic: client.publicKey)
        XCTAssertEqual(a.hex, v["session_key"] as? String)
        XCTAssertEqual(b.hex, a.hex)
    }

    func testCanonicalJSONMatchesPythonAndJavaScript() throws {
        let cases = try XCTUnwrap(v["canonical"] as? [[String: Any]])
        XCTAssertEqual(cases.count, 3)
        for c in cases {
            let got = String(decoding: try RemoteProtocol.canonical(c["object"]), as: UTF8.self)
            XCTAssertEqual(got, c["canonical"] as? String)
        }
        XCTAssertThrowsError(try RemoteProtocol.canonical(["x": 1.5]))
        XCTAssertThrowsError(try RemoteProtocol.canonical(["é": 1]))
        XCTAssertEqual(String(decoding: try RemoteProtocol.canonical(["b": true, "a": 2, "c": NSNull()]), as: UTF8.self),
                       "{\"a\":2,\"b\":true,\"c\":null}")
    }

    func testSignaturesMatchTheVectors() throws {
        let signed = try XCTUnwrap(v["signed"] as? [String: [String: Any]])
        let request = try XCTUnwrap(signed["session.request"])
        var body = request
        body["sig"] = nil
        let client = try identity("client"), host = try identity("host")
        let mine = try RemoteProtocol.sign(body, identity: client, peerPublic: host.publicKey)
        XCTAssertEqual(mine["sig"] as? String, request["sig"] as? String)
        XCTAssertNoThrow(try RemoteProtocol.verify(request, identity: host, peerPublic: client.publicKey, now: ts))
        for kind in ["session.accept", "sdp.offer"] {
            XCTAssertNoThrow(try RemoteProtocol.verify(signed[kind]!, identity: client, peerPublic: host.publicKey, now: ts))
        }
    }

    func testRejectedMessages() throws {
        let rejected = try XCTUnwrap(v["rejected"] as? [[String: Any]])
        let expected: [String: RemoteProtocol.Failure] = ["bad_signature": .badSignature, "wrong_sender": .wrongSender]
        for c in rejected {
            let verifier = try identity(c["verifier"] as! String), peer = try identity(c["peer"] as! String)
            XCTAssertThrowsError(try RemoteProtocol.verify(c["message"] as! [String: Any], identity: verifier,
                                                           peerPublic: peer.publicKey, now: ts)) { error in
                XCTAssertEqual(error as? RemoteProtocol.Failure, expected[c["why"] as! String])
            }
        }
        // the phone's own request reflected back as if the PC had sent it
        let request = (v["signed"] as! [String: [String: Any]])["session.request"]!
        XCTAssertThrowsError(try RemoteProtocol.verify(request, identity: try identity("client"),
                                                       peerPublic: try identity("host").publicKey, now: ts)) {
            XCTAssertEqual($0 as? RemoteProtocol.Failure, .wrongSender)
        }
        XCTAssertThrowsError(try RemoteProtocol.verify(request, identity: try identity("host"),
                                                       peerPublic: try identity("client").publicKey,
                                                       now: ts.addingTimeInterval(301))) {
            XCTAssertEqual($0 as? RemoteProtocol.Failure, .stale)
        }
    }

    func testRelayLoginProof() throws {
        let rl = try XCTUnwrap(v["relay_login"] as? [String: Any])
        let proof = try RemoteProtocol.relayProof(identity: try identity("client"), challenge: rl["challenge"] as! [String: Any],
                                                  role: rl["role"] as! String)
        XCTAssertEqual(proof["sig"] as? String, (rl["proof"] as? [String: Any])?["sig"] as? String)
    }

    func testPairingMAC() throws {
        let pv = try XCTUnwrap(v["pairing"] as? [String: Any])
        let token = try Pairing.decodeCode(pv["code"] as! String)
        XCTAssertEqual(token.hex, pv["token"] as? String)
        let psk = Pairing.psk(forToken: token)
        XCTAssertEqual(psk.hex, pv["psk"] as? String)
        let request = pv["request"] as! [String: Any]
        XCTAssertNoThrow(try RemoteProtocol.checkPairMAC(request, psk: psk, now: ts))
        XCTAssertEqual(try RemoteProtocol.pairMAC(request, psk: psk)["mac"] as? String, request["mac"] as? String)
        XCTAssertThrowsError(try RemoteProtocol.checkPairMAC(request, psk: Data(count: 32), now: ts))
    }
}

final class RemoteSessionTests: XCTestCase {
    let host = LinkIdentity(privateKey: hexData("a8abababababababababababababababababababababababababababababab6b"),
                            publicKey: try! SaintCrypto.publicKey(forPrivate: hexData("a8abababababababababababababababababababababababababababababab6b")),
                            name: "Halo PC")
    let phone: LinkIdentity = {
        let p = SaintCrypto.generateKeyPair()
        return LinkIdentity(privateKey: p.privateKey, publicKey: p.publicKey, name: "iPhone")
    }()

    func fromHost(_ machine: RemoteSessionMachine, _ type: String, _ fields: [String: Any]) throws -> [String: Any] {
        var f = fields
        f["session_id"] = machine.sessionID
        return try RemoteProtocol.sign(RemoteProtocol.routed(type, from: host.deviceID, to: phone.deviceID, f),
                                       identity: host, peerPublic: phone.publicKey)
    }

    func testRequestAcceptOfferAnswer() throws {
        let m = RemoteSessionMachine(identity: phone, hostPublicKey: host.publicKey)
        let req = try m.request(name: "iPhone", maxWidth: 1170, maxHeight: 2532)
        XCTAssertNoThrow(try RemoteProtocol.verify(req, identity: host, peerPublic: phone.publicKey))
        XCTAssertEqual(m.state, .waiting)
        let accept = try fromHost(m, "session.accept", ["ice_servers": [["urls": ["turn:r:3478"], "username": "u", "credential": "c"]]])
        XCTAssertEqual(m.handle(accept), [.accepted(iceServers: [.init(urls: ["turn:r:3478"], username: "u", credential: "c")])])
        XCTAssertEqual(m.handle(try fromHost(m, "sdp.offer", ["sdp": "v=0"])), [.offer(sdp: "v=0")])
        let answer = try m.answer(sdp: "v=0 a")
        XCTAssertEqual(try RemoteProtocol.verify(answer, identity: host, peerPublic: phone.publicKey)["sdp"] as? String, "v=0 a")
        let ice: [String: Any] = ["type": "ice", "from": host.deviceID, "to": phone.deviceID, "session_id": m.sessionID,
                                  "candidate": ["candidate": "candidate:1", "sdpMLineIndex": 0]]
        XCTAssertEqual(m.handle(ice), [.candidate(sdp: "candidate:1", mLineIndex: 0)])
        m.connected()
        XCTAssertEqual(m.state, .connected)
        let end = try XCTUnwrap(try m.end(reason: "closed"))
        XCTAssertEqual(end["type"] as? String, "session.end")
        XCTAssertNil(try m.end(reason: "again"))
    }

    func testForgedAndForeignMessagesAreIgnored() throws {
        let m = RemoteSessionMachine(identity: phone, hostPublicKey: host.publicKey)
        _ = try m.request(name: "iPhone", maxWidth: 100, maxHeight: 100)
        var accept = try fromHost(m, "session.accept", ["ice_servers": []])
        accept["ice_servers"] = [["urls": ["turn:evil:3478"]]]           // altered by the relay
        XCTAssertEqual(m.handle(accept), [])
        let stranger = SaintCrypto.generateKeyPair()
        let strangerID = LinkIdentity(privateKey: stranger.privateKey, publicKey: stranger.publicKey, name: "x")
        let forged = try RemoteProtocol.sign(RemoteProtocol.routed("session.deny", from: host.deviceID, to: phone.deviceID,
                                                                   ["session_id": m.sessionID, "reason": "x"]),
                                             identity: strangerID, peerPublic: phone.publicKey)
        XCTAssertEqual(m.handle(forged), [])
        XCTAssertEqual(m.state, .waiting)
        // an offer before the accept is out of order
        XCTAssertEqual(m.handle(try fromHost(m, "sdp.offer", ["sdp": "v=0"])), [])
        XCTAssertEqual(m.handle(try fromHost(m, "session.deny", ["reason": "declined"])), [.ended(reason: "declined")])
        XCTAssertTrue(m.isEnded)
    }

    func testPresenceParsing() {
        let hosts = RemoteHost.parse(presence: ["type": "presence", "hosts": [
            ["account": "a", "device_id": "bd7381eab08d2d62", "name": "Halo PC", "online": true, "last_seen": 1793600000],
            ["name": "broken"]]])
        XCTAssertEqual(hosts.count, 1)
        XCTAssertEqual(hosts[0].name, "Halo PC")
        XCTAssertTrue(hosts[0].online)
    }
}

/// The relay client against a scripted socket: login with the vectors' challenge, presence, routed messages.
final class RemoteRelayClientTests: XCTestCase {
    final class ScriptedSocket: RemoteSocket {
        var incoming: [String] = []
        var sent: [[String: Any]] = []
        func send(_ text: String) async throws {
            sent.append(try JSONSerialization.jsonObject(with: Data(text.utf8)) as! [String: Any])
        }
        func receive() async throws -> String {
            if incoming.isEmpty { throw URLError(.networkConnectionLost) }
            return incoming.removeFirst()
        }
        func close() {}
    }

    func testLoginPresenceAndMessages() async throws {
        let v = try TestData.object("remote_vectors")
        let k = (v["keys"] as! [String: [String: String]])["client"]!
        let priv = hexData(k["private"]!)
        let me = LinkIdentity(privateKey: priv, publicKey: try SaintCrypto.publicKey(forPrivate: priv), name: "iPhone")
        let rl = v["relay_login"] as! [String: Any]
        let socket = ScriptedSocket()
        func text(_ o: [String: Any]) -> String { String(decoding: try! JSONSerialization.data(withJSONObject: o), as: UTF8.self) }
        socket.incoming = [text(rl["challenge"] as! [String: Any]),
                           text(["type": "welcome", "device_id": me.deviceID, "ice_servers": [["urls": ["stun:r:3478"]]]]),
                           text(["type": "presence", "hosts": [["account": "acc", "device_id": "bd7381eab08d2d62", "name": "PC", "online": true]]]),
                           text(["type": "ice", "from": "bd7381eab08d2d62", "to": me.deviceID, "session_id": "s", "candidate": [:]])]
        let client = RemoteRelayClient(identity: me, accounts: ["acc"], makeSocket: { socket })
        var hosts: [RemoteHost] = [], messages: [[String: Any]] = [], statuses: [RemoteRelayClient.Status] = []
        client.onPresence = { hosts = $0 }
        client.onMessage = { messages.append($0) }
        client.onStatus = { statuses.append($0) }
        do { try await client.runOnce() } catch {}             // ends when the script runs out
        XCTAssertEqual(socket.sent.first?["type"] as? String, "hello")
        XCTAssertEqual(socket.sent.first?["accounts"] as? [String], ["acc"])
        XCTAssertEqual(socket.sent[1]["sig"] as? String, (rl["proof"] as! [String: Any])["sig"] as? String)
        XCTAssertEqual(hosts.map(\.name), ["PC"])
        XCTAssertEqual(messages.count, 1)
        XCTAssertEqual(statuses, [.connecting, .online])
        XCTAssertEqual(client.iceServers.count, 1)
    }
}

final class RemoteInputTests: XCTestCase {
    func t(_ id: Int, _ px: Double, _ py: Double) -> RemoteTouch { RemoteTouch(id: id, x: px / 1000, y: py / 500, px: px, py: py) }
    func sends(_ out: [GestureOutput]) -> [[String: Any]] {
        out.compactMap { if case .send(let m) = $0 { return m } else { return nil } }
    }

    func testTapLongPressDrag() {
        let g = TouchGestures()
        _ = g.feed(.start, [t(1, 100, 100)], at: 0)
        let tap = sends(g.feed(.end, [], at: 0.1))
        XCTAssertEqual(tap.map { $0["type"] as? String }, ["pointer.move", "pointer.button", "pointer.button"])
        XCTAssertEqual(tap[1]["button"] as? Int, 1)

        _ = g.feed(.start, [t(1, 100, 100)], at: 1)
        XCTAssertTrue(g.tick(at: 1.2).isEmpty)
        let long = sends(g.tick(at: 1.6))
        XCTAssertEqual(long[1]["button"] as? Int, 3)
        XCTAssertTrue(g.feed(.end, [], at: 2).isEmpty)

        _ = g.feed(.start, [t(1, 100, 100)], at: 3)
        let drag = sends(g.feed(.move, [t(1, 150, 100)], at: 3.1))
        XCTAssertEqual(drag[1]["down"] as? Bool, true)
        XCTAssertTrue(g.tick(at: 9).isEmpty)
        XCTAssertEqual(sends(g.feed(.end, [], at: 4)).first?["down"] as? Bool, false)
    }

    func testTwoFingersAndPinch() {
        let g = TouchGestures()
        _ = g.feed(.start, [t(1, 100, 100), t(2, 200, 100)], at: 0)
        XCTAssertEqual(sends(g.feed(.end, [], at: 0.1)).first?["button"] as? Int, 3)
        _ = g.feed(.start, [t(1, 100, 100), t(2, 200, 100)], at: 1)
        let scroll = sends(g.feed(.move, [t(1, 100, 60), t(2, 200, 60)], at: 1.05))
        XCTAssertEqual(scroll.first?["dy"] as? Int, 80)
        _ = g.feed(.end, [], at: 1.5)
        _ = g.feed(.start, [t(1, 400, 200), t(2, 500, 200)], at: 2)
        let out = g.feed(.move, [t(1, 350, 200), t(2, 550, 200)], at: 2.05)
        if case .view(let zoom, _, _)? = out.first { XCTAssertEqual(zoom, 2) } else { XCTFail("expected a zoom") }
        XCTAssertTrue(sends(out).isEmpty)
    }

    func testLetterboxAndKeys() {
        let p = RemoteInput.normalized(x: 500, y: 500, viewWidth: 1000, viewHeight: 1000, videoWidth: 1920, videoHeight: 1080)
        XCTAssertEqual(p?.x ?? -1, 0.5, accuracy: 1e-9)
        XCTAssertEqual(p?.y ?? -1, 0.5, accuracy: 1e-9)
        let combo = RemoteInput.combo(["Control", "Alt", "Delete"])
        XCTAssertEqual(combo.map { $0["code"] as? Int }, [29, 56, 111, 111, 56, 29])
        XCTAssertEqual(combo.map { $0["down"] as? Bool }, [true, true, true, false, false, false])
        XCTAssertEqual(RemoteInput.text("é").first?["utf8"] as? String, "é")
        XCTAssertEqual(RemoteInput.text("\n").count, 2)
    }
}
