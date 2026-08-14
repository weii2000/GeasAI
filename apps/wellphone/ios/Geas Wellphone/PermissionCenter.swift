@preconcurrency import Contacts
@preconcurrency import CoreLocation
@preconcurrency import HealthKit
import Observation
@preconcurrency import Photos
import WorkoutKit

@MainActor
@Observable
final class PermissionCenter: NSObject, CLLocationManagerDelegate {
    private(set) var photoStatus = "检查中"
    private(set) var locationStatus = "检查中"
    private(set) var healthStatus = "检查中"
    private(set) var contactStatus = "检查中"
    private(set) var workoutStatus = "检查中"
    private(set) var errorMessage: String?

    private let locationManager = CLLocationManager()
    private let healthStore = HKHealthStore()

    override init() {
        super.init()
        locationManager.delegate = self
    }

    func refresh() async {
        photoStatus = switch PHPhotoLibrary.authorizationStatus(for: .readWrite) {
        case .authorized: "已完整授权"
        case .limited: "仅部分照片"
        case .denied, .restricted: "未授权"
        case .notDetermined: "尚未请求"
        @unknown default: "未知"
        }
        locationStatus = switch locationManager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse: "已授权"
        case .denied, .restricted: "未授权"
        case .notDetermined: "尚未请求"
        @unknown default: "未知"
        }
        contactStatus = switch CNContactStore.authorizationStatus(for: .contacts) {
        case .authorized: "已授权"
        case .denied, .restricted, .limited: "未授权"
        case .notDetermined: "尚未请求"
        @unknown default: "未知"
        }
        if HKHealthStore.isHealthDataAvailable() {
            let status = try? await healthStore.statusForAuthorizationRequest(
                toShare: [],
                read: HealthService.readTypes
            )
            healthStatus = status == .unnecessary ? "无需再次请求" : "需要请求"
        } else {
            healthStatus = "设备不支持"
        }
        guard WorkoutScheduler.isSupported else {
            workoutStatus = "未配对可用 Apple Watch"
            return
        }
        workoutStatus = switch await WorkoutScheduler.shared.authorizationState {
        case .authorized: "已授权"
        case .denied, .restricted: "未授权"
        case .notDetermined: "尚未请求"
        @unknown default: "未知"
        }
    }

    func requestPhotos() async {
        _ = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        await refresh()
    }

    func requestLocation() {
        locationManager.requestWhenInUseAuthorization()
    }

    func requestHealth() async {
        do {
            try await healthStore.requestAuthorization(
                toShare: [],
                read: HealthService.readTypes
            )
        } catch {
            errorMessage = error.localizedDescription
        }
        await refresh()
    }

    func requestContacts() async {
        do {
            _ = try await CNContactStore().requestAccess(for: .contacts)
        } catch {
            errorMessage = error.localizedDescription
        }
        await refresh()
    }

    func requestWorkout() async {
        guard WorkoutScheduler.isSupported else {
            errorMessage = WellphoneError.workoutUnavailable(
                "需要已配对且安装“体能训练”的 Apple Watch"
            ).localizedDescription
            return
        }
        _ = await WorkoutScheduler.shared.requestAuthorization()
        await refresh()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { await refresh() }
    }
}
