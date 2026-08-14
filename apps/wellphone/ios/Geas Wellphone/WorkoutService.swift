@preconcurrency import HealthKit
import CryptoKit
import Foundation
import WorkoutKit

@MainActor
final class WorkoutService {
    private let scheduler = WorkoutScheduler.shared

    func scheduledWorkouts() async throws -> [JSONValue] {
        try await requireAuthorization()
        return await scheduler.scheduledWorkouts.map(scheduledJSON)
    }

    func schedule(
        actionID: String,
        activityName: String,
        locationName: String,
        goalType: String,
        goalValue: Double?,
        date: Date
    ) async throws -> (id: String, alreadyScheduled: Bool) {
        try await requireAuthorization()
        let id = deterministicUUID(actionID)
        if await scheduler.scheduledWorkouts.contains(where: { $0.plan.id == id }) {
            return (id.uuidString.lowercased(), true)
        }

        let activity = try activity(activityName)
        let location = try workoutLocation(locationName)
        let goal = try workoutGoal(type: goalType, value: goalValue)
        guard SingleGoalWorkout.supportsActivity(activity),
              SingleGoalWorkout.supportsGoal(
                goal,
                activity: activity,
                location: location
              ) else {
            throw WellphoneError.invalidArguments("该运动不支持指定目标")
        }
        let swimmingLocation: HKWorkoutSwimmingLocationType = switch locationName {
        case "indoor": .pool
        case "outdoor": .openWater
        default: .unknown
        }
        let workout = SingleGoalWorkout(
            activity: activity,
            location: location,
            swimmingLocation: activity == .swimming ? swimmingLocation : .unknown,
            goal: goal
        )
        let plan = WorkoutPlan(.goal(workout), id: id)
        await scheduler.schedule(plan, at: dateComponents(date))
        guard await scheduler.scheduledWorkouts.contains(where: { $0.plan.id == id }) else {
            throw WellphoneError.workoutUnavailable("训练计划未写入 Apple Watch")
        }
        return (id.uuidString.lowercased(), false)
    }

    func remove(id: UUID) async throws -> Bool {
        try await requireAuthorization()
        guard let scheduled = await scheduler.scheduledWorkouts.first(where: {
            $0.plan.id == id
        }) else {
            return false
        }
        await scheduler.remove(scheduled.plan, at: scheduled.date)
        let stillScheduled = await scheduler.scheduledWorkouts.contains {
            $0.plan.id == id
        }
        return !stillScheduled
    }

    private func requireAuthorization() async throws {
        guard WorkoutScheduler.isSupported else {
            throw WellphoneError.workoutUnavailable("需要已配对且安装“体能训练”的 Apple Watch")
        }
        guard await scheduler.authorizationState == .authorized else {
            throw WellphoneError.permissionRequired("训练计划")
        }
    }

    private func scheduledJSON(_ scheduled: ScheduledWorkoutPlan) -> JSONValue {
        let goalWorkout: SingleGoalWorkout? = if case .goal(let workout) = scheduled.plan.workout {
            workout
        } else {
            nil
        }
        return .object([
            "workout_id": .string(scheduled.plan.id.uuidString.lowercased()),
            "activity": .string(activityName(scheduled.plan.workout.activity)),
            "scheduled_at": Calendar.current.date(from: scheduled.date).map {
                .string($0.ISO8601Format())
            } ?? .null,
            "complete": .bool(scheduled.complete),
            "goal": goalWorkout.map { goalJSON($0.goal) } ?? .null,
        ])
    }

    private func workoutGoal(type: String, value: Double?) throws -> WorkoutGoal {
        switch type {
        case "open":
            return .open
        case "time_minutes":
            return .time(try positive(value), .minutes)
        case "distance_km":
            return .distance(try positive(value), .kilometers)
        case "energy_kcal":
            return .energy(try positive(value), .kilocalories)
        default:
            throw WellphoneError.invalidArguments("不支持的训练目标")
        }
    }

    private func goalJSON(_ goal: WorkoutGoal) -> JSONValue {
        switch goal {
        case .open:
            .object(["type": .string("open")])
        case .time(let value, let unit):
            .object([
                "type": .string("time_minutes"),
                "value": .number(unit.converter.baseUnitValue(fromValue: value) / 60),
            ])
        case .distance(let value, let unit):
            .object([
                "type": .string("distance_km"),
                "value": .number(unit.converter.baseUnitValue(fromValue: value) / 1_000),
            ])
        case .energy(let value, let unit):
            .object([
                "type": .string("energy_kcal"),
                "value": .number(
                    Measurement(value: value, unit: unit).converted(to: .kilocalories).value
                ),
            ])
        default:
            .object(["type": .string("other")])
        }
    }

    private func activity(_ name: String) throws -> HKWorkoutActivityType {
        switch name {
        case "walking": .walking
        case "running": .running
        case "cycling": .cycling
        case "swimming": .swimming
        default: throw WellphoneError.invalidArguments("不支持的运动类型")
        }
    }

    private func activityName(_ activity: HKWorkoutActivityType) -> String {
        switch activity {
        case .walking: "walking"
        case .running: "running"
        case .cycling: "cycling"
        case .swimming: "swimming"
        default: "other_\(activity.rawValue)"
        }
    }

    private func workoutLocation(_ name: String) throws -> HKWorkoutSessionLocationType {
        switch name {
        case "indoor": .indoor
        case "outdoor": .outdoor
        case "unknown": .unknown
        default: throw WellphoneError.invalidArguments("不支持的训练地点")
        }
    }

    private func positive(_ value: Double?) throws -> Double {
        guard let value, value > 0 else {
            throw WellphoneError.invalidArguments("非开放目标必须提供正数 goal_value")
        }
        return value
    }

    private func dateComponents(_ date: Date) -> DateComponents {
        var components = Calendar.current.dateComponents(
            [.year, .month, .day, .hour, .minute],
            from: date
        )
        components.calendar = .current
        components.timeZone = .current
        return components
    }

    private func deterministicUUID(_ value: String) -> UUID {
        let hex = SHA256.hash(data: Data(value.utf8)).prefix(16).map {
            String(format: "%02x", $0)
        }.joined()
        let formatted = "\(hex.prefix(8))-\(hex.dropFirst(8).prefix(4))-" +
            "\(hex.dropFirst(12).prefix(4))-\(hex.dropFirst(16).prefix(4))-" +
            "\(hex.dropFirst(20).prefix(12))"
        return UUID(uuidString: formatted)!
    }
}
