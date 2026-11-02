import Foundation
import WebRTC
import SaintCore

/// One receive-only WebRTC connection to a Halo PC (Google's WebRTC.framework via the stasel/WebRTC package).
/// The PC sends the offer (video + the "control" and "files" data channels); this side answers.
///
/// Written without a Mac to compile it on: see mobile/README.md (Halo Remote) for what has been verified.
final class WebRTCPeer: NSObject {
    private static let factory: RTCPeerConnectionFactory = {
        RTCInitializeSSL()
        return RTCPeerConnectionFactory(encoderFactory: RTCDefaultVideoEncoderFactory(),
                                        decoderFactory: RTCDefaultVideoDecoderFactory())
    }()

    var onVideoTrack: ((RTCVideoTrack) -> Void)?
    var onCandidate: ((String, Int32) -> Void)?
    var onState: ((RTCPeerConnectionState) -> Void)?
    var onControl: (([String: Any]) -> Void)?
    var onFilesMessage: (([String: Any]) -> Void)?

    private var pc: RTCPeerConnection?
    private(set) var control: RTCDataChannel?
    private(set) var files: RTCDataChannel?
    private var pendingCandidates: [RTCIceCandidate] = []
    private var haveRemote = false
    private let constraints = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil)

    init(iceServers: [RemoteSessionMachine.IceServer]) {
        super.init()
        let config = RTCConfiguration()
        config.iceServers = iceServers.map { RTCIceServer(urlStrings: $0.urls, username: $0.username, credential: $0.credential) }
        config.sdpSemantics = .unifiedPlan
        config.bundlePolicy = .maxBundle
        config.continualGatheringPolicy = .gatherContinually
        pc = Self.factory.peerConnection(with: config, constraints: constraints, delegate: self)
    }

    /// The PC's offer -> our answer SDP.
    func answer(offer sdp: String) async throws -> String {
        guard let pc = pc else { throw URLError(.cancelled) }
        try await pc.setRemoteDescription(RTCSessionDescription(type: .offer, sdp: sdp))
        haveRemote = true
        for c in pendingCandidates { try? await pc.add(c) }
        pendingCandidates.removeAll()
        let answer = try await pc.answer(for: constraints)
        try await pc.setLocalDescription(answer)
        return answer.sdp
    }

    func addCandidate(sdp: String, mLineIndex: Int32) {
        let c = RTCIceCandidate(sdp: sdp, sdpMLineIndex: mLineIndex, sdpMid: nil)
        guard haveRemote, let pc = pc else { pendingCandidates.append(c); return }
        pc.add(c) { _ in }
    }

    func sendControl(_ message: [String: Any]) {
        guard let ch = control, ch.readyState == .open,
              let data = try? JSONSerialization.data(withJSONObject: message) else { return }
        _ = ch.sendData(RTCDataBuffer(data: data, isBinary: false))
    }

    func sendFiles(text message: [String: Any]) -> Bool {
        guard let ch = files, ch.readyState == .open, let data = try? JSONSerialization.data(withJSONObject: message) else { return false }
        return ch.sendData(RTCDataBuffer(data: data, isBinary: false))
    }

    func sendFiles(binary data: Data) -> Bool {
        guard let ch = files, ch.readyState == .open else { return false }
        return ch.sendData(RTCDataBuffer(data: data, isBinary: true))
    }

    var filesBuffered: UInt64 { files?.bufferedAmount ?? 0 }

    /// (round-trip ms, frames per second, relayed through TURN?) from the connection's statistics.
    func stats() async -> (rtt: Int, fps: Int, relayed: Bool) {
        guard let pc = pc else { return (0, 0, false) }
        return await withCheckedContinuation { cont in
            pc.statistics { report in
                var rtt = 0, fps = 0, relayed = false
                let all = report.statistics
                for (_, s) in all {
                    if s.type == "inbound-rtp", (s.values["kind"] as? String) == "video" {
                        fps = (s.values["framesPerSecond"] as? NSNumber)?.intValue ?? fps
                    }
                    if s.type == "candidate-pair", (s.values["state"] as? String) == "succeeded",
                       (s.values["nominated"] as? NSNumber)?.boolValue == true {
                        rtt = Int(((s.values["currentRoundTripTime"] as? NSNumber)?.doubleValue ?? 0) * 1000)
                        for key in ["localCandidateId", "remoteCandidateId"] {
                            if let id = s.values[key] as? String, (all[id]?.values["candidateType"] as? String) == "relay" {
                                relayed = true
                            }
                        }
                    }
                }
                cont.resume(returning: (rtt, fps, relayed))
            }
        }
    }

    func close() {
        control?.close(); files?.close()
        pc?.close()
        pc = nil
    }
}

extension WebRTCPeer: RTCPeerConnectionDelegate {
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange stateChanged: RTCSignalingState) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd stream: RTCMediaStream) {
        if let track = stream.videoTracks.first { DispatchQueue.main.async { self.onVideoTrack?(track) } }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove stream: RTCMediaStream) {}
    func peerConnectionShouldNegotiate(_ peerConnection: RTCPeerConnection) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceConnectionState) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCIceGatheringState) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didGenerate candidate: RTCIceCandidate) {
        DispatchQueue.main.async { self.onCandidate?(candidate.sdp, candidate.sdpMLineIndex) }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didRemove candidates: [RTCIceCandidate]) {}
    func peerConnection(_ peerConnection: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {
        dataChannel.delegate = self
        DispatchQueue.main.async {
            if dataChannel.label == "control" { self.control = dataChannel }
            if dataChannel.label == "files" { self.files = dataChannel }
        }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didChange newState: RTCPeerConnectionState) {
        DispatchQueue.main.async { self.onState?(newState) }
    }
    func peerConnection(_ peerConnection: RTCPeerConnection, didAdd rtpReceiver: RTCRtpReceiver, streams mediaStreams: [RTCMediaStream]) {
        if let track = rtpReceiver.track as? RTCVideoTrack { DispatchQueue.main.async { self.onVideoTrack?(track) } }
    }
}

extension WebRTCPeer: RTCDataChannelDelegate {
    func dataChannelDidChangeState(_ dataChannel: RTCDataChannel) {}
    func dataChannel(_ dataChannel: RTCDataChannel, didReceiveMessageWith buffer: RTCDataBuffer) {
        guard !buffer.isBinary, let m = try? JSONSerialization.jsonObject(with: buffer.data) as? [String: Any] else { return }
        let label = dataChannel.label
        DispatchQueue.main.async {
            if label == "control" { self.onControl?(m) } else { self.onFilesMessage?(m) }
        }
    }
}
