@preconcurrency import BackgroundTasks
import Foundation
import Observation
import UIKit
import UserNotifications

@MainActor
@Observable
final class JobCoordinator {
    var serverAddress = UserDefaults.standard.string(
        forKey: "wellphone.serverAddress"
    ) ?? "http://192.168.1.10:8000" {
        didSet {
            UserDefaults.standard.set(serverAddress, forKey: "wellphone.serverAddress")
        }
    }
    private(set) var isRunning = false
    private(set) var status = "尚未开始"
    private(set) var answer = ""
    private(set) var errorMessage: String?
    private(set) var messages: [ConversationMessage] = []
    private(set) var activities: [TaskActivity] = []
    private(set) var sessionID = UserDefaults.standard.string(
        forKey: "wellphone.sessionID"
    )
    private(set) var pendingApproval: ToolApproval?
    private(set) var mailDraft: MailDraft?
    private(set) var pendingActions: [PendingAction] = {
        guard let data = UserDefaults.standard.data(forKey: "wellphone.pendingActions") else {
            return []
        }
        return (try? JSONDecoder().decode([PendingAction].self, from: data)) ?? []
    }()

    private let deviceID = DeviceIdentity.loadOrCreate()
    private let executor = ToolExecutor()
    private let scheduler = BGTaskScheduler.shared
    private let notificationCenter = UNUserNotificationCenter.current()
    private let backgroundIdentifier: String
    private var registered = false
    private var runTask: Task<Void, Never>?
    private var client: APIClient?
    private var serverTaskID: String?
    private var backgroundTask: BGContinuedProcessingTask?
    private var completedSteps: Int64 = 0
    private var approvalContinuation: CheckedContinuation<Bool, Never>?
    private var runActions: [PendingAction] = []

    init() {
        let bundleID = Bundle.main.bundleIdentifier ?? "com.example.Wellphone"
        backgroundIdentifier = "\(bundleID).wellphone-task"
        registerBackgroundTask()
    }

    func restoreSession() async {
        guard !isRunning, let sessionID, let url = URL(string: serverAddress) else {
            return
        }
        do {
            let client = try APIClient(baseURL: url, deviceID: deviceID)
            messages = try await client.serverSession(id: sessionID).messages
        } catch {
            status = "无法恢复上次对话"
        }
    }

    func newConversation() {
        guard !isRunning else { return }
        sessionID = nil
        messages = []
        activities = []
        answer = ""
        errorMessage = nil
        status = "新对话"
        UserDefaults.standard.removeObject(forKey: "wellphone.sessionID")
    }

    func start(prompt: String) {
        guard !isRunning else { return }
        errorMessage = nil
        activities = []
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanPrompt.isEmpty else {
            errorMessage = "请输入任务。"
            return
        }
        guard let url = URL(string: serverAddress) else {
            errorMessage = WellphoneError.invalidServerURL.localizedDescription
            return
        }

        messages.append(
            ConversationMessage(
                id: UUID().uuidString,
                role: .user,
                content: cleanPrompt,
                timestamp: Date.now.ISO8601Format()
            )
        )
        isRunning = true
        status = "正在准备…"
        answer = ""
        activities = [
            TaskActivity(
                id: "planning",
                title: "Agent 规划任务",
                state: .running
            )
        ]
        completedSteps = 0
        runActions = []
        executor.resetScope()

        runTask = Task {
            do {
                try await prepareBackgroundExecution()
                await requestNotificationAuthorization()
                let client = try APIClient(baseURL: url, deviceID: deviceID)
                self.client = client
                try await run(prompt: cleanPrompt, client: client)
            } catch is CancellationError {
                status = "任务已取消"
                finishRunningActivity(as: .cancelled, detail: "已取消")
                await cancelOnServerIfNeeded()
                finish(success: false)
            } catch {
                status = "任务失败"
                errorMessage = error.localizedDescription
                finishRunningActivity(as: .failed)
                await syncSessionIfPossible()
                await cancelOnServerIfNeeded()
                finish(success: false)
            }
        }
    }

    func cancel() {
        guard isRunning else { return }
        status = "正在取消…"
        resolveApproval(false)
        if let client, let serverTaskID {
            Task.detached {
                try? await client.cancel(taskID: serverTaskID)
            }
        }
        runTask?.cancel()
    }

    func answerApproval(_ approved: Bool) {
        resolveApproval(approved)
    }

    func dismissMailDraft(result: String? = nil) {
        mailDraft = nil
        if let result {
            status = result
        }
    }

    private func run(prompt: String, client: APIClient) async throws {
        let requestedID = UUID().uuidString.lowercased()
        serverTaskID = requestedID
        let created = try await client.createTask(
            id: requestedID,
            sessionID: sessionID,
            prompt: prompt,
            deviceContext: """
            当前时间：\(Date.now.ISO8601Format())
            时区：\(TimeZone.current.identifier)
            """
        )
        guard created.id == requestedID else {
            throw WellphoneError.server("Server 返回了错误的任务 ID")
        }
        sessionID = created.sessionID
        UserDefaults.standard.set(created.sessionID, forKey: "wellphone.sessionID")
        try Task.checkCancellation()
        status = "Agent 正在规划…"

        while !Task.isCancelled {
            let current = try await client.task(id: created.id)
            switch current.status {
            case .completed:
                answer = current.answer ?? "任务已完成。"
                finishRunningActivity(as: .completed)
                await syncSessionIfPossible()
                let actions = publishRunActions()
                await notify(actions)
                status = actions.isEmpty ? "已完成" : "已完成，等待你处理"
                finish(success: true)
                return
            case .failed:
                throw WellphoneError.server(current.error ?? "Agent 任务失败")
            case .cancelled:
                status = "任务已取消"
                finishRunningActivity(as: .cancelled, detail: "已取消")
                finish(success: false)
                return
            case .running, .waitingForPhone:
                break
            }

            let poll = try await client.nextTool(taskID: created.id)
            guard let call = poll.toolCall else {
                advanceProgress(title: "Agent 正在规划…", amount: 1)
                continue
            }
            finishRunningActivity(as: .completed)
            let toolName = ToolName(rawValue: call.name)
            let toolTitle = toolName?.displayName ?? call.name
            updateActivity(id: call.callID, title: toolTitle, state: .running)
            status = "正在执行：\(toolTitle)"
            let result = await executor.execute(
                call,
                onProgress: { detail in
                    self.updateActivity(
                        id: call.callID,
                        title: toolTitle,
                        detail: detail,
                        state: .running
                    )
                    self.advanceProgress(title: detail, amount: 1)
                },
                approve: { approval in
                    await self.requestApproval(approval)
                },
                onPendingAction: { action in
                    self.prepareAction(action)
                }
            )
            try Task.checkCancellation()
            try await client.submit(taskID: created.id, result: result)
            updateActivity(
                id: call.callID,
                title: toolTitle,
                detail: result.isError ? "执行失败，Agent 正在处理" : nil,
                state: result.isError ? .failed : .completed
            )
            advanceProgress(title: "已完成：\(toolTitle)")
            status = "Agent 正在规划下一步…"
        }
        throw CancellationError()
    }

    private func requestApproval(_ approval: ToolApproval) async -> Bool {
        status = "等待你确认：\(approval.title)"
        advanceProgress(title: "需要确认，请返回 Wellphone", amount: 1)
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                resolveApproval(false)
                pendingApproval = approval
                approvalContinuation = continuation
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.resolveApproval(false)
            }
        }
    }

    private func resolveApproval(_ approved: Bool) {
        let continuation = approvalContinuation
        approvalContinuation = nil
        pendingApproval = nil
        continuation?.resume(returning: approved)
    }

    private func syncSessionIfPossible() async {
        guard let client, let sessionID,
              let session = try? await client.serverSession(id: sessionID) else {
            return
        }
        messages = session.messages
    }

    func performPendingAction(id: String) async {
        guard let action = pendingActions.first(where: { $0.id == id }) else {
            return
        }
        switch action.kind {
        case .mail:
            guard let draft = action.mailDraft else { return }
            mailDraft = draft
            removePendingAction(id: id)
        case .url:
            guard let url = action.url,
                  url.scheme == "https",
                  ["www.google.com", "www.youtube.com"].contains(url.host),
                  await UIApplication.shared.open(url) else {
                errorMessage = WellphoneError.externalAppUnavailable.localizedDescription
                return
            }
            removePendingAction(id: id)
        }
    }

    func dismissPendingAction(id: String) {
        removePendingAction(id: id)
    }

    func consumeSelectedNotification() async {
        let defaults = UserDefaults.standard
        guard let id = defaults.string(
            forKey: WellphoneNotification.selectedActionKey
        ) else {
            return
        }
        defaults.removeObject(forKey: WellphoneNotification.selectedActionKey)
        await performPendingAction(id: id)
    }

    private func registerBackgroundTask() {
        guard !registered else { return }
        registered = scheduler.register(
            forTaskWithIdentifier: backgroundIdentifier,
            using: .main
        ) { [weak self] task in
            MainActor.assumeIsolated {
                guard let self,
                      let task = task as? BGContinuedProcessingTask else {
                    task.setTaskCompleted(success: false)
                    return
                }
                self.attach(task)
            }
        }
        if !registered {
            errorMessage = WellphoneError.backgroundRegistrationFailed.localizedDescription
        }
    }

    private func submitBackgroundTask() async throws {
        guard registered else {
            throw WellphoneError.backgroundRegistrationFailed
        }
        let request = BGContinuedProcessingTaskRequest(
            identifier: backgroundIdentifier,
            title: "Wellphone 正在处理",
            subtitle: "准备开始"
        )
        request.strategy = .fail
        try scheduler.submit(request)

        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: .seconds(5))
        while backgroundTask == nil {
            try Task.checkCancellation()
            guard clock.now < deadline else {
                scheduler.cancel(taskRequestWithIdentifier: backgroundIdentifier)
                throw WellphoneError.backgroundStartTimedOut
            }
            try await Task.sleep(for: .milliseconds(50))
        }
    }

    private func prepareBackgroundExecution() async throws {
        do {
            try await submitBackgroundTask()
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            updateActivity(
                id: "background",
                title: "后台执行暂不可用",
                detail: "请暂时保持 Wellphone 打开",
                state: .failed
            )
        }
    }

    private func requestNotificationAuthorization() async {
        _ = try? await notificationCenter.requestAuthorization(
            options: [.alert, .sound]
        )
    }

    private func prepareAction(_ action: PendingAction) {
        if let index = runActions.firstIndex(where: { $0.id == action.id }) {
            runActions[index] = action
        } else {
            runActions.append(action)
        }
    }

    private func publishRunActions() -> [PendingAction] {
        for action in runActions {
            if let index = pendingActions.firstIndex(where: { $0.id == action.id }) {
                pendingActions[index] = action
            } else {
                pendingActions.append(action)
            }
        }
        savePendingActions()
        let actions = runActions
        runActions = []
        return actions
    }

    private func notify(_ actions: [PendingAction]) async {
        for action in actions {
            let content = UNMutableNotificationContent()
            content.title = action.title
            content.body = action.detail
            content.sound = .default
            content.categoryIdentifier = WellphoneNotification.categoryID
            content.userInfo = [WellphoneNotification.actionIDKey: action.id]
            try? await notificationCenter.add(
                UNNotificationRequest(
                    identifier: action.id,
                    content: content,
                    trigger: nil
                )
            )
        }
    }

    private func removePendingAction(id: String) {
        pendingActions.removeAll { $0.id == id }
        savePendingActions()
        notificationCenter.removeDeliveredNotifications(withIdentifiers: [id])
    }

    private func savePendingActions() {
        guard let data = try? JSONEncoder().encode(pendingActions) else { return }
        UserDefaults.standard.set(data, forKey: "wellphone.pendingActions")
    }

    private func attach(_ task: BGContinuedProcessingTask) {
        guard isRunning else {
            task.setTaskCompleted(success: false)
            return
        }
        backgroundTask = task
        task.progress.totalUnitCount = 100
        task.progress.completedUnitCount = completedSteps
        task.expirationHandler = { [weak self] in
            Task { @MainActor in
                self?.cancel()
            }
        }
    }

    private func advanceProgress(title: String, amount: Int64 = 8) {
        completedSteps = min(completedSteps + amount, 92)
        backgroundTask?.progress.completedUnitCount = completedSteps
        backgroundTask?.updateTitle("Wellphone 正在处理", subtitle: title)
    }

    private func updateActivity(
        id: String,
        title: String,
        detail: String? = nil,
        state: TaskActivity.State
    ) {
        if let index = activities.firstIndex(where: { $0.id == id }) {
            activities[index].title = title
            activities[index].detail = detail
            activities[index].state = state
        } else {
            activities.append(
                TaskActivity(id: id, title: title, detail: detail, state: state)
            )
        }
    }

    private func finishRunningActivity(
        as state: TaskActivity.State,
        detail: String? = nil
    ) {
        guard let index = activities.lastIndex(where: { $0.state == .running }) else {
            return
        }
        activities[index].state = state
        if let detail {
            activities[index].detail = detail
        }
    }

    private func finish(success: Bool) {
        guard isRunning else { return }
        resolveApproval(false)
        isRunning = false
        runTask = nil
        client = nil
        serverTaskID = nil
        if !success {
            runActions = []
        }

        if let task = backgroundTask {
            if success {
                task.progress.completedUnitCount = 100
                task.updateTitle("Wellphone 已完成", subtitle: answer)
            }
            task.setTaskCompleted(success: success)
            backgroundTask = nil
        }
    }

    private func cancelOnServerIfNeeded() async {
        guard let client, let serverTaskID else { return }
        try? await client.cancel(taskID: serverTaskID)
    }

}
