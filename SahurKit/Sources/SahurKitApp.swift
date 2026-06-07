import SwiftUI

@main
struct SahurKitApp: App {
    @StateObject private var session = SahurSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
                .onAppear { session.start() }
        }
    }
}

struct ContentView: View {
    @EnvironmentObject var session: SahurSession

    var body: some View {
        VStack(spacing: 16) {
            Text("🪵 SahurKit")
                .font(.largeTitle).bold()
            Text(session.statusText)
                .font(.headline)
                .foregroundColor(.secondary)
            Text("Agent: \(session.agentState)")
                .font(.subheadline)
            if !session.lastCaption.isEmpty {
                Text(session.lastCaption)
                    .font(.body)
                    .padding()
                    .background(Color.black.opacity(0.05))
                    .cornerRadius(12)
            }
            Button(session.micEnabled ? "Mute" : "Talk") {
                session.toggleTurn()
            }
            .buttonStyle(.borderedProminent)
            Text("Tap Sahur on the home screen to talk.\nKeep this app open (it runs the voice link in the background).")
                .font(.caption)
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
        }
        .padding()
    }
}
