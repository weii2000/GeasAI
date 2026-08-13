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
    private(set) var messages: [ConversationMessage] = []
    private(set) var sessionID = UserDefaults.standard.string(
        forKey: "wellphone.sessionID"
    )
    private(set) var pendingApproval: ToolApproval?
    private(set) var mailDraft: MailDraft?

    private let deviceID = DeviceIdentity.loadOrCreate()
    private let executor = ToolExecutor()
    private let scheduler = BGTaskScheduler.shared
    private let backgroundIdentifier: String
    private var registered = false
    private var runTask: Task<Void, Never>?
    private var client: APIClient?
    private var serverTaskID: String?
    private var backgroundTask: BGContinuedProcessingTask?
    private var completedSteps: Int64 = 0
    private var approvalContinuation: CheckedContinuation<Bool, Never>?

    init() {
        let bundleID = Bundle.main.bundleIdentifier ?? "com.example.Wellphone"
        backgroundIdentifier = "\(bundleID).photo-agent"
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
        answer = ""
        errorMessage = nil
        status = "新对话"
        UserDefaults.standard.removeObject(forKey: "wellphone.sessionID")
    }

    func start(prompt: String) {
        guard !isRunning else { return }
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanPrompt.isEmpty else {
            errorMessage = "请输入任务。"
            return
        }
        guard let url = URL(string: serverAddress) else {
            errorMessage = WellphoneError.invalidServerURL.localizedDescription
            return
        }

        UserDefaults.standard.set(serverAddress, forKey: "wellphone.serverAddress")
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
        errorMessage = nil
        completedSteps = 0
        executor.resetScope()

        runTask = Task {
            do {
                try await submitBackgroundTask()
                try Task.checkCancellation()
                let client = try APIClient(baseURL: url, deviceID: deviceID)
                self.client = client
                try await run(prompt: cleanPrompt, client: client)
            } catch is CancellationError {
                status = "任务已取消"
                await cancelOnServerIfNeeded()
                finish(success: false)
            } catch {
                status = "任务失败"
                errorMessage = error.localizedDescription
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
        status = "Agent 正在规划…你可以切换到其他 App"
        advanceProgress(title: "Wellphone 正在处理")

        while !Task.isCancelled {
            let current = try await client.task(id: created.id)
            switch current.status {
            case .completed:
                answer = current.answer ?? "任务已完成。"
                await syncSessionIfPossible()
                status = mailDraft == nil ? "已完成" : "邮件草稿已准备，请确认发送"
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
            let result = await executor.execute(
                call,
                onProgress: { detail in
                    self.advanceProgress(title: detail, amount: 1)
                },
                approve: { approval in
                    await self.requestApproval(approval)
                },
                onMailDraft: { draft in
                    self.mailDraft = draft
                }
            )
            try Task.checkCancellation()
            try await client.submit(taskID: created.id, result: result)
            advanceProgress(title: "已完成：\(displayName(for: call.name))")
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

    private func finish(success: Bool) {
        guard isRunning else { return }
        resolveApproval(false)
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
        case "get_photo_details": "读取照片信息"
        case "analyze_photos": "设备端识别照片"
        case "list_albums": "读取相册"
        case "find_album": "查找相册"
        case "create_album": "创建相册"
        case "rename_album": "重命名相册"
        case "delete_album": "删除相册"
        case "add_photos_to_album": "加入相册"
        case "remove_photos_from_album": "移出相册"
        case "get_album_contents": "核对相册"
        case "set_favorite": "修改收藏"
        case "set_hidden": "修改隐藏状态"
        case "set_photo_creation_date": "修改照片日期"
        case "set_photo_location": "修改照片位置"
        case "delete_photos": "删除照片"
        case "compose_email": "准备邮件草稿"
        default: tool
        }
    }
}
