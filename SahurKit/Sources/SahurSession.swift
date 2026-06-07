import Foundation
import AVFoundation
import Combine
import LiveKit

// Set this to your laptop's IP running token_server.py (same wifi as the phone).
// Or hardcode a url+token from the LiveKit sandbox for the quickest demo.
enum Config {
    static let tokenURL = "http://192.168.1.50:8788/token?identity=phone&room=sahur"
    static let room = "sahur"
    // Optional hardcoded fallback (skip token_server): fill both to use directly.
    static let hardcodedURL: String? = nil      // e.g. "wss://xxx.livekit.cloud"
    static let hardcodedToken: String? = nil
}

private let kCaptionPath = "/var/mobile/Library/Caches/sahur_caption.txt"

@MainActor
final class SahurSession: ObservableObject {
    static weak var shared: SahurSession?

    @Published var statusText = "starting…"
    @Published var agentState = "idle"
    @Published var lastCaption = ""
    @Published var micEnabled = false

    private let room = Room()
    private var started = false

    init() { SahurSession.shared = self; registerToggleObserver() }

    func start() {
        guard !started else { return }
        started = true
        configureAudioSession()
        room.add(delegate: self)
        Task { await connect() }
    }

    private func configureAudioSession() {
        // Keep the voice link alive while the user is in another app.
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playAndRecord, mode: .voiceChat,
                           options: [.defaultToSpeaker, .allowBluetooth, .mixWithOthers])
        try? s.setActive(true)
    }

    private func connect() async {
        do {
            let (url, token) = try await fetchCredentials()
            try await room.connect(url: url, token: token)
            statusText = "connected"
            // Start unmuted so the agent greets and listens immediately.
            try await room.localParticipant.setMicrophone(enabled: true)
            micEnabled = true
            postState("listening")
        } catch {
            statusText = "connect failed: \(error.localizedDescription)"
        }
    }

    private func fetchCredentials() async throws -> (String, String) {
        if let u = Config.hardcodedURL, let t = Config.hardcodedToken { return (u, t) }
        let (data, _) = try await URLSession.shared.data(from: URL(string: Config.tokenURL)!)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        return (json["url"] as! String, json["token"] as! String)
    }

    // Tapping Sahur toggles the mic (push-to-talk feel).
    func toggleTurn() {
        Task {
            let enable = !micEnabled
            try? await room.localParticipant.setMicrophone(enabled: enable)
            micEnabled = enable
            postState(enable ? "listening" : "idle")
        }
    }

    // ---- Darwin IPC -------------------------------------------------------

    private func registerToggleObserver() {
        let center = CFNotificationCenterGetDarwinNotifyCenter()
        let cb: CFNotificationCallback = { _, _, _, _, _ in
            Task { @MainActor in SahurSession.shared?.toggleTurn() }
        }
        CFNotificationCenterAddObserver(center, Unmanaged.passUnretained(self).toOpaque(), cb,
            "com.sahur.toggle" as CFString, nil, .deliverImmediately)
    }

    func postState(_ state: String) {
        agentState = state
        let name = "com.sahur.state.\(state)" as CFString
        CFNotificationCenterPostNotification(CFNotificationCenterGetDarwinNotifyCenter(),
            CFNotificationName(name), nil, nil, true)
    }

    func publishCaption(_ text: String) {
        lastCaption = text
        // Best-effort: write where the SpringBoard tweak can read it (works if the
        // app is signed with broad filesystem entitlements; harmless if it fails).
        try? text.write(toFile: kCaptionPath, atomically: true, encoding: .utf8)
        CFNotificationCenterPostNotification(CFNotificationCenterGetDarwinNotifyCenter(),
            CFNotificationName("com.sahur.caption" as CFString), nil, nil, true)
    }
}

// ---- LiveKit room events --------------------------------------------------

extension SahurSession: RoomDelegate {
    nonisolated func room(_ room: Room, didUpdateConnectionState connectionState: ConnectionState,
                          from oldConnectionState: ConnectionState) {
        Task { @MainActor in self.statusText = "\(connectionState)" }
    }

    // Agent publishes its state via the participant attribute "lk.agent.state".
    nonisolated func room(_ room: Room, participant: Participant,
                          didUpdateAttributes attributes: [String: String]) {
        guard let s = attributes["lk.agent.state"] else { return }
        let mapped: String
        switch s {
        case "listening": mapped = "listening"
        case "thinking":  mapped = "thinking"
        case "speaking":  mapped = "speaking"
        default:          mapped = "idle"
        }
        Task { @MainActor in self.postState(mapped) }
    }

    // Live transcript -> caption bubble (signature may vary slightly by SDK version).
    nonisolated func room(_ room: Room, participant: Participant?,
                          didReceiveTranscriptionSegments segments: [TranscriptionSegment]) {
        guard let last = segments.last else { return }
        Task { @MainActor in self.publishCaption(last.text) }
    }
}
