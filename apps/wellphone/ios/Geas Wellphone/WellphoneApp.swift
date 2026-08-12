import SwiftUI

@main
struct WellphoneApp: App {
    @State private var coordinator = JobCoordinator()

    var body: some Scene {
        WindowGroup {
            ContentView(coordinator: coordinator)
        }
    }
}
