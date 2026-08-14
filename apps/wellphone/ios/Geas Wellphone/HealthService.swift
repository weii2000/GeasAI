@preconcurrency import HealthKit
import Foundation

@MainActor
final class HealthService {
    static let readTypes: Set<HKObjectType> = {
        let identifiers: [HKQuantityTypeIdentifier] = [
            .stepCount,
            .distanceWalkingRunning,
            .activeEnergyBurned,
            .appleExerciseTime,
            .flightsClimbed,
        ]
        var types = Set<HKObjectType>(identifiers.compactMap {
            HKQuantityType.quantityType(forIdentifier: $0)
        })
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) {
            types.insert(sleep)
        }
        types.insert(HKObjectType.workoutType())
        return types
    }()

    private let store = HKHealthStore()

    func activitySummary(start: Date, end: Date) async throws -> [String: JSONValue] {
        try await requirePreparedAuthorization()
        let metrics: [(String, HKQuantityTypeIdentifier, HKUnit)] = [
            ("steps", .stepCount, .count()),
            ("walking_running_distance_km", .distanceWalkingRunning, .meterUnit(with: .kilo)),
            ("active_energy_kcal", .activeEnergyBurned, .kilocalorie()),
            ("exercise_minutes", .appleExerciseTime, .minute()),
            ("flights_climbed", .flightsClimbed, .count()),
        ]
        let predicate = HKQuery.predicateForSamples(
            withStart: start,
            end: end,
            options: .strictStartDate
        )
        var totals: [String: JSONValue] = [:]
        for (name, identifier, unit) in metrics {
            try Task.checkCancellation()
            guard let type = HKQuantityType.quantityType(forIdentifier: identifier) else {
                continue
            }
            let descriptor = HKStatisticsQueryDescriptor(
                predicate: .quantitySample(type: type, predicate: predicate),
                options: .cumulativeSum
            )
            let statistics = try await descriptor.result(for: store)
            let value = statistics?.sumQuantity()?.doubleValue(for: unit) ?? 0
            totals[name] = .number(value)
        }
        return [
            "start": .string(start.ISO8601Format()),
            "end": .string(end.ISO8601Format()),
            "totals": .object(totals),
            "privacy_note": .string(
                "HealthKit returns no distinction between denied read access and no data."
            ),
        ]
    }

    func sleepSummary(start: Date, end: Date) async throws -> [String: JSONValue] {
        try await requirePreparedAuthorization()
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else {
            throw WellphoneError.healthUnavailable
        }
        let predicate = HKQuery.predicateForSamples(
            withStart: start,
            end: end,
            options: .strictStartDate
        )
        let descriptor = HKSampleQueryDescriptor(
            predicates: [.categorySample(type: type, predicate: predicate)],
            sortDescriptors: [SortDescriptor(\.startDate)],
            limit: nil
        )
        let samples = try await descriptor.result(for: store)
        var all: [DateInterval] = []
        var stages: [String: [DateInterval]] = [:]
        for sample in samples {
            guard let value = HKCategoryValueSleepAnalysis(rawValue: sample.value),
                  HKCategoryValueSleepAnalysis.allAsleepValues.contains(value) else {
                continue
            }
            let interval = DateInterval(start: sample.startDate, end: sample.endDate)
            all.append(interval)
            stages[stageName(value), default: []].append(interval)
        }
        return [
            "start": .string(start.ISO8601Format()),
            "end": .string(end.ISO8601Format()),
            "total_sleep_hours": .number(mergedDuration(all) / 3_600),
            "stages_hours": .object(stages.mapValues {
                .number(mergedDuration($0) / 3_600)
            }),
            "privacy_note": .string(
                "An empty result can mean no data or that read access was denied."
            ),
        ]
    }

    func workouts(start: Date, end: Date, limit: Int) async throws -> [JSONValue] {
        try await requirePreparedAuthorization()
        let predicate = HKQuery.predicateForSamples(
            withStart: start,
            end: end,
            options: .strictStartDate
        )
        let descriptor = HKSampleQueryDescriptor(
            predicates: [.workout(predicate)],
            sortDescriptors: [SortDescriptor(\.startDate, order: .reverse)],
            limit: limit
        )
        return try await descriptor.result(for: store).map { workout in
            let energyType = HKQuantityType.quantityType(
                forIdentifier: .activeEnergyBurned
            )!
            return .object([
                "workout_id": .string(workout.uuid.uuidString.lowercased()),
                "activity": .string(activityName(workout.workoutActivityType)),
                "start": .string(workout.startDate.ISO8601Format()),
                "end": .string(workout.endDate.ISO8601Format()),
                "duration_minutes": .number(workout.duration / 60),
                "distance_km": workout.totalDistance.map {
                    .number($0.doubleValue(for: .meterUnit(with: .kilo)))
                } ?? .null,
                "energy_kcal": workout.statistics(for: energyType)?.sumQuantity().map {
                    .number($0.doubleValue(for: .kilocalorie()))
                } ?? .null,
            ])
        }
    }

    private func requirePreparedAuthorization() async throws {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw WellphoneError.healthUnavailable
        }
        let status = try await store.statusForAuthorizationRequest(
            toShare: [],
            read: Self.readTypes
        )
        guard status == .unnecessary else {
            throw WellphoneError.permissionRequired("健康数据")
        }
    }

    private func stageName(_ value: HKCategoryValueSleepAnalysis) -> String {
        switch value {
        case .asleepCore: "core"
        case .asleepDeep: "deep"
        case .asleepREM: "rem"
        case .asleepUnspecified: "unspecified"
        default: "other"
        }
    }

    private func mergedDuration(_ intervals: [DateInterval]) -> TimeInterval {
        let sorted = intervals.sorted { $0.start < $1.start }
        guard var current = sorted.first else { return 0 }
        var total: TimeInterval = 0
        for interval in sorted.dropFirst() {
            if interval.start <= current.end {
                current = DateInterval(
                    start: current.start,
                    end: max(current.end, interval.end)
                )
            } else {
                total += current.duration
                current = interval
            }
        }
        return total + current.duration
    }

    private func activityName(_ activity: HKWorkoutActivityType) -> String {
        switch activity {
        case .walking: "walking"
        case .running: "running"
        case .cycling: "cycling"
        case .swimming: "swimming"
        case .hiking: "hiking"
        case .yoga: "yoga"
        case .rowing: "rowing"
        case .elliptical: "elliptical"
        case .highIntensityIntervalTraining: "high_intensity_interval_training"
        case .functionalStrengthTraining: "functional_strength_training"
        case .traditionalStrengthTraining: "traditional_strength_training"
        default: "other_\(activity.rawValue)"
        }
    }
}
