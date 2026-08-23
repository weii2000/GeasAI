@preconcurrency import EventKit
import Foundation

@MainActor
final class ReminderService {
    private static let actionIDsKey = "wellphone.reminderActionIDs"

    private let store = EKEventStore()
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func create(
        actionID: String,
        title: String,
        notes: String?,
        dueAt: Date?,
        priorityName: String
    ) async throws -> [String: JSONValue] {
        try await requireAccess()

        var actionIDs = defaults.dictionary(forKey: Self.actionIDsKey)
            as? [String: String] ?? [:]
        if let identifier = actionIDs[actionID] {
            guard let reminder = store.calendarItem(
                withIdentifier: identifier
            ) as? EKReminder else {
                throw WellphoneError.invalidArguments(
                    "此前已创建的提醒无法核验，为避免重复未再次创建"
                )
            }
            return result(
                reminder,
                priorityName: priorityName,
                alreadyCreated: true
            )
        }

        guard let list = store.defaultCalendarForNewReminders() else {
            throw WellphoneError.invalidArguments("没有可用的默认提醒列表")
        }
        let reminder = EKReminder(eventStore: store)
        reminder.title = title
        reminder.notes = notes
        reminder.calendar = list
        reminder.priority = try priority(priorityName)
        if let dueAt {
            var components = Calendar.current.dateComponents(
                [.year, .month, .day, .hour, .minute, .second],
                from: dueAt
            )
            components.calendar = .current
            components.timeZone = .current
            reminder.dueDateComponents = components
            reminder.addAlarm(EKAlarm(absoluteDate: dueAt))
        }

        try store.save(reminder, commit: true)
        let identifier = reminder.calendarItemIdentifier
        let expectedPriority = try priority(priorityName)
        guard !identifier.isEmpty,
              let saved = store.calendarItem(
                withIdentifier: identifier
              ) as? EKReminder,
              saved.title == title,
              saved.notes == notes,
              saved.priority == expectedPriority,
              datesMatch(saved.dueDateComponents?.date, dueAt),
              dueAt == nil || saved.alarms?.contains(where: {
                  datesMatch($0.absoluteDate, dueAt)
              }) == true else {
            throw WellphoneError.invalidArguments("提醒保存后无法核验")
        }
        actionIDs[actionID] = identifier
        defaults.set(actionIDs, forKey: Self.actionIDsKey)
        return result(
            saved,
            priorityName: priorityName,
            alreadyCreated: false
        )
    }

    private func requireAccess() async throws {
        switch EKEventStore.authorizationStatus(for: .reminder) {
        case .fullAccess:
            return
        case .notDetermined:
            guard try await store.requestFullAccessToReminders() else {
                throw WellphoneError.permissionRequired("提醒事项")
            }
        case .denied, .restricted, .writeOnly:
            throw WellphoneError.permissionRequired("提醒事项")
        @unknown default:
            throw WellphoneError.permissionRequired("提醒事项")
        }
    }

    private func priority(_ name: String) throws -> Int {
        switch name {
        case "none": Int(EKReminderPriority.none.rawValue)
        case "low": Int(EKReminderPriority.low.rawValue)
        case "medium": Int(EKReminderPriority.medium.rawValue)
        case "high": Int(EKReminderPriority.high.rawValue)
        default: throw WellphoneError.invalidArguments("提醒优先级无效")
        }
    }

    private func result(
        _ reminder: EKReminder,
        priorityName: String,
        alreadyCreated: Bool
    ) -> [String: JSONValue] {
        let dueAt = reminder.dueDateComponents?.date?.ISO8601Format()
        return [
            "reminder_id": .string(reminder.calendarItemIdentifier),
            "created": .bool(true),
            "already_created": .bool(alreadyCreated),
            "list_name": .string(reminder.calendar.title),
            "due_at": dueAt.map(JSONValue.string) ?? .null,
            "priority": .string(priorityName),
        ]
    }

    private func datesMatch(_ first: Date?, _ second: Date?) -> Bool {
        switch (first, second) {
        case (nil, nil): true
        case let (first?, second?): abs(first.timeIntervalSince(second)) < 1
        default: false
        }
    }
}
