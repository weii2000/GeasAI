@preconcurrency import CoreLocation
import Foundation
import MapKit

@MainActor
final class LocationService {
    func currentLocation() async throws -> [String: JSONValue] {
        let location = try await OneShotLocationRequest().start()
        let address = try? await reverseGeocode(location: location).first
        return locationObject(location, name: address?.name, address: address?.address)
    }

    func geocode(address: String, limit: Int) async throws -> [JSONValue] {
        guard (1...5).contains(limit),
              let request = MKGeocodingRequest(addressString: address) else {
            throw WellphoneError.invalidArguments("地址或 max_results 无效")
        }
        let items = try await withTaskCancellationHandler {
            try await request.mapItems
        } onCancel: {
            request.cancel()
        }
        return items.prefix(limit).map(mapItemJSON)
    }

    func reverseGeocode(latitude: Double, longitude: Double) async throws -> [JSONValue] {
        try coordinates(latitude: latitude, longitude: longitude)
        let location = CLLocation(latitude: latitude, longitude: longitude)
        return try await reverseGeocode(location: location).map {
            .object(locationObject(location, name: $0.name, address: $0.address))
        }
    }

    func nearby(query: String, radius: Double, limit: Int) async throws -> [JSONValue] {
        guard (100...50_000).contains(radius), (1...10).contains(limit) else {
            throw WellphoneError.invalidArguments("附近搜索范围或数量无效")
        }
        let location = try await OneShotLocationRequest().start()
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = query
        request.resultTypes = [.address, .pointOfInterest]
        request.region = MKCoordinateRegion(
            center: location.coordinate,
            latitudinalMeters: radius * 2,
            longitudinalMeters: radius * 2
        )
        let search = MKLocalSearch(request: request)
        let response = try await withTaskCancellationHandler {
            try await search.start()
        } onCancel: {
            search.cancel()
        }
        return response.mapItems.prefix(limit).map(mapItemJSON)
    }

    private func reverseGeocode(location: CLLocation) async throws -> [Place] {
        guard let request = MKReverseGeocodingRequest(location: location) else {
            throw WellphoneError.invalidArguments("无法解析该坐标")
        }
        let items = try await withTaskCancellationHandler {
            try await request.mapItems
        } onCancel: {
            request.cancel()
        }
        return items.map {
            Place(name: $0.name, address: $0.address?.fullAddress)
        }
    }

    private func mapItemJSON(_ item: MKMapItem) -> JSONValue {
        .object(locationObject(
            item.location,
            name: item.name,
            address: item.address?.fullAddress
        ))
    }

    private func locationObject(
        _ location: CLLocation,
        name: String?,
        address: String?
    ) -> [String: JSONValue] {
        [
            "name": name.map(JSONValue.string) ?? .null,
            "address": address.map(JSONValue.string) ?? .null,
            "latitude": .number(location.coordinate.latitude),
            "longitude": .number(location.coordinate.longitude),
            "horizontal_accuracy_meters": .number(location.horizontalAccuracy),
            "timestamp": .string(location.timestamp.ISO8601Format()),
        ]
    }

    private func coordinates(latitude: Double, longitude: Double) throws {
        guard (-90...90).contains(latitude), (-180...180).contains(longitude) else {
            throw WellphoneError.invalidArguments("经纬度超出范围")
        }
    }
}

private struct Place {
    let name: String?
    let address: String?
}

@MainActor
private final class OneShotLocationRequest: NSObject, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private var continuation: CheckedContinuation<CLLocation, Error>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func start() async throws -> CLLocation {
        guard CLLocationManager.locationServicesEnabled(),
              [.authorizedAlways, .authorizedWhenInUse].contains(
                manager.authorizationStatus
              ) else {
            throw WellphoneError.permissionRequired("定位")
        }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                self.continuation = continuation
                manager.requestLocation()
            }
        } onCancel: {
            Task { @MainActor in self.finish(throwing: CancellationError()) }
        }
    }

    func locationManager(
        _ manager: CLLocationManager,
        didUpdateLocations locations: [CLLocation]
    ) {
        guard let location = locations.last(where: { $0.horizontalAccuracy >= 0 }) else {
            finish(throwing: WellphoneError.invalidArguments("没有可用的位置结果"))
            return
        }
        finish(returning: location)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        finish(throwing: error)
    }

    private func finish(returning location: CLLocation) {
        let continuation = continuation
        self.continuation = nil
        manager.stopUpdatingLocation()
        continuation?.resume(returning: location)
    }

    private func finish(throwing error: Error) {
        let continuation = continuation
        self.continuation = nil
        manager.stopUpdatingLocation()
        continuation?.resume(throwing: error)
    }
}
