import SwiftUI
@preconcurrency import UserNotifications
import UIKit

@main
struct WellphoneApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var coordinator = JobCoordinator()

    var body: some Scene {
        WindowGroup {
            ContentView(coordinator: coordinator)
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.setNotificationCategories([
            UNNotificationCategory(
                identifier: WellphoneNotification.categoryID,
                actions: [
                    UNNotificationAction(
                        identifier: WellphoneNotification.openActionID,
                        title: "打开",
                        options: .foreground
                    )
                ],
                intentIdentifiers: []
            )
        ])
        return true
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        guard response.actionIdentifier != UNNotificationDismissActionIdentifier,
              let id = response.notification.request.content.userInfo[
                WellphoneNotification.actionIDKey
              ] as? String else {
            return
        }
        UserDefaults.standard.set(id, forKey: WellphoneNotification.selectedActionKey)
        await MainActor.run {
            NotificationCenter.default.post(name: WellphoneNotification.selected, object: nil)
        }
    }
}
