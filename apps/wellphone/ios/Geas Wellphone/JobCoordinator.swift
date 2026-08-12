@preconcurrency import BackgroundTasks
import Foundation
import Observation

@MainActor
@Observable
final class JobCoordinator {
    var serverAddress = UserDefaults.standard.string(
        forKey: "wellphone.serverAddress"
    ) ?? "http://192.168.1.10:8000"
    private(set) var isRunning = false
    private(set) var status = "尚未开始"
    private(set) var answer = ""
    private(set) var errorMessage: String?

    private let executor = ToolExecutor()
    private let scheduler = BGTaskScheduler.shared
    private let backgroundIdentifier: String
    private var registered = false
    private var runTask: Task<Void, Never>?
    private var client: APIClient?
    private var serverTaskID: String?
    private var backgroundTask: BGContinuedProcessingTask?
    private var completedSteps: Int64 = 0

    init() {
        let bundleID = Bundle.main.bundleIdentifier ?? "com.example.Wellphone"
        backgroundIdentifier = "\(bundleID).photo-agent"
        registerBackgroundTask()
    }

    func start(prompt: String) {
        guard !isRunning else { return }
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanPrompt.isEmpty else {
            errorMessage = "请输入照片整理任务。"
            return
        }
        guard let url = URL(string: serverAddress) else {
            errorMessage = WellphoneError.invalidServerURL.localizedDescription
            return
        }

        UserDefaults.standard.set(serverAddress, forKey: "wellphone.serverAddress")
        isRunning = true
        status = "正在申请照片权限…"
        answer = ""
        errorMessage = nil
        completedSteps = 0
        executor.resetScope()

        runTask = Task {
            do {
                try await executor.requirePermission()
                try Task.checkCancellation()
                try await submitBackgroundTask()
                try Task.checkCancellation()
                let client = try APIClient(baseURL: url)
                self.client = client
                try Task.checkCancellation()
                try await run(prompt: cleanPrompt, client: client)
            } catch is CancellationError {
                status = "任务已取消"
                await cancelOnServerIfNeeded()
                finish(success: false)
            } catch {
                status = "任务失败"
                errorMessage = error.localizedDescription
                await cancelOnServerIfNeeded()
                finish(success: false)
            }
        }
    }

    func cancel() {
        guard isRunning else { return }
        status = "正在取消…"
        if let client, let serverTaskID {
            Task.detached {
                try? await client.cancel(taskID: serverTaskID)
            }
        }
        runTask?.cancel()
    }

    private func run(prompt: String, client: APIClient) async throws {
        let contextualPrompt = """
        \(prompt)

        设备当前时间：\(Date.now.ISO8601Format())
        设备时区：\(TimeZone.current.identifier)
        """
        try Task.checkCancellation()
        let requestedID = UUID().uuidString.lowercased()
        serverTaskID = requestedID
        let created = try await client.createTask(
            id: requestedID,
            prompt: contextualPrompt
        )
        guard created.id == requestedID else {
            throw WellphoneError.server("Server 返回了错误的任务 ID")
        }
        try Task.checkCancellation()
        status = "Agent 正在规划…你可以切换到其他 App"
        advanceProgress(title: "Wellphone 正在整理照片")

        while !Task.isCancelled {
            let current = try await client.task(id: created.id)
            switch current.status {
            case .completed:
                answer = current.answer ?? "任务已完成。"
                status = "已完成"
                finish(success: true)
                return
            case .failed:
                throw WellphoneError.server(current.error ?? "Agent 任务失败")
            case .running, .waitingForPhone:
                break
            }

            let poll = try await client.nextTool(taskID: created.id)
            guard let call = poll.toolCall else {
                advanceProgress(title: "Agent 正在规划…", amount: 1)
                continue
            }
            status = "正在执行：\(displayName(for: call.name))"
            let result = await executor.execute(call) { detail in
                advanceProgress(title: detail, amount: 1)
            }
            try Task.checkCancellation()
            try await client.submit(taskID: created.id, result: result)
            advanceProgress(title: "已完成：\(displayName(for: call.name))")
        }
        throw CancellationError()
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
            title: "Wellphone 正在整理照片",
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
        backgroundTask?.updateTitle(
            "Wellphone 正在整理照片",
            subtitle: title
        )
    }

    private func finish(success: Bool) {
        guard isRunning else { return }
        isRunning = false
        runTask = nil
        client = nil
        serverTaskID = nil

        if let task = backgroundTask {
            if success {
                task.progress.completedUnitCount = 100
                task.updateTitle("Wellphone 已完成", subtitle: answer)
            }
            task.setTaskCompleted(success: success)
            backgroundTask = nil
        } else {
            scheduler.cancel(taskRequestWithIdentifier: backgroundIdentifier)
        }
    }

    private func cancelOnServerIfNeeded() async {
        guard let client, let serverTaskID else { return }
        try? await client.cancel(taskID: serverTaskID)
    }

    private func displayName(for tool: String) -> String {
        switch tool {
        case "search_photos": "查找照片"
        case "analyze_photos": "设备端识别照片"
        case "create_album": "创建相册"
        case "add_photos_to_album": "加入相册"
        case "get_album_contents": "核对结果"
        default: tool
        }
    }
}
