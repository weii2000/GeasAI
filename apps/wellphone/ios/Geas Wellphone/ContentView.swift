import AVFoundation
import MessageUI
import Observation
import Speech
import SwiftUI

struct ContentView: View {
    @Bindable var coordinator: JobCoordinator
    @State private var prompt = ""
    @State private var speech = SpeechInput()

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                        if coordinator.messages.isEmpty {
                            ContentUnavailableView(
                                "开始一个任务",
                                systemImage: "sparkles",
                                description: Text("整理照片、修改相册，或起草一封邮件。")
                            )
                            .padding(.top, 60)
                        }
                        ForEach(coordinator.messages) { message in
                            MessageBubble(message: message)
                        }

                        if coordinator.isRunning {
                            HStack(spacing: 10) {
                                ProgressView()
                                Text(coordinator.status)
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                                Spacer()
                            }
                            .padding(.horizontal)
                        }

                        if let error = coordinator.errorMessage {
                            Text(error)
                                .font(.callout)
                                .foregroundStyle(.red)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal)
                        }
                        }
                        .padding(.vertical)
                    }
                    .onChange(of: coordinator.messages.count) {
                        guard let last = coordinator.messages.last else { return }
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }

                Divider()

                VStack(spacing: 10) {
                    TextField("连接到 Mac，例如 http://192.168.1.10:8000", text: $coordinator.serverAddress)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .font(.caption)
                        .disabled(coordinator.isRunning)

                    HStack(alignment: .bottom, spacing: 10) {
                        TextField("给 Wellphone 发消息…", text: $prompt, axis: .vertical)
                            .lineLimit(1...5)
                            .textFieldStyle(.roundedBorder)
                            .disabled(coordinator.isRunning)

                        Button {
                            if speech.isListening {
                                speech.stop()
                            } else {
                                Task { await speech.start { prompt = $0 } }
                            }
                        } label: {
                            Image(systemName: speech.isListening ? "stop.circle.fill" : "mic.fill")
                                .font(.title2)
                        }
                        .tint(speech.isListening ? .red : .accentColor)
                        .disabled(coordinator.isRunning)

                        if coordinator.isRunning {
                            Button(role: .destructive) {
                                coordinator.cancel()
                            } label: {
                                Image(systemName: "stop.fill")
                                    .font(.title2)
                            }
                        } else {
                            Button {
                                let text = prompt
                                prompt = ""
                                speech.stop()
                                coordinator.start(prompt: text)
                            } label: {
                                Image(systemName: "arrow.up.circle.fill")
                                    .font(.title)
                            }
                            .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }

                    if let error = speech.errorMessage {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding()
                .background(.bar)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Wellphone")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("新对话", systemImage: "square.and.pencil") {
                        coordinator.newConversation()
                    }
                    .disabled(coordinator.isRunning)
                }
            }
            .task { await coordinator.restoreSession() }
            .onDisappear { speech.stop() }
            .alert(
                coordinator.pendingApproval?.title ?? "确认操作",
                isPresented: approvalPresented,
                presenting: coordinator.pendingApproval
            ) { approval in
                Button("取消", role: .cancel) {
                    coordinator.answerApproval(false)
                }
                Button("允许", role: approval.destructive ? .destructive : nil) {
                    coordinator.answerApproval(true)
                }
            } message: { approval in
                Text(approval.message)
            }
            .sheet(item: mailDraft) { draft in
                MailComposer(draft: draft) { result in
                    coordinator.dismissMailDraft(result: result)
                }
            }
        }
    }

    private var approvalPresented: Binding<Bool> {
        Binding(
            get: { coordinator.pendingApproval != nil },
            set: { if !$0 { coordinator.answerApproval(false) } }
        )
    }

    private var mailDraft: Binding<MailDraft?> {
        Binding(
            get: { coordinator.mailDraft },
            set: { if $0 == nil { coordinator.dismissMailDraft() } }
        )
    }
}

private struct MessageBubble: View {
    let message: ConversationMessage

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 52) }
            Text(message.content)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .foregroundStyle(message.role == .user ? .white : .primary)
                .background(
                    message.role == .user ? Color.accentColor : Color(.secondarySystemGroupedBackground),
                    in: RoundedRectangle(cornerRadius: 18)
                )
            if message.role == .assistant { Spacer(minLength: 52) }
        }
        .padding(.horizontal)
    }
}

private struct MailComposer: UIViewControllerRepresentable {
    let draft: MailDraft
    let onFinish: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onFinish: onFinish)
    }

    func makeUIViewController(context: Context) -> MFMailComposeViewController {
        let controller = MFMailComposeViewController()
        controller.mailComposeDelegate = context.coordinator
        controller.setToRecipients(draft.to)
        controller.setCcRecipients(draft.cc)
        controller.setBccRecipients(draft.bcc)
        controller.setSubject(draft.subject)
        controller.setMessageBody(draft.body, isHTML: false)
        return controller
    }

    func updateUIViewController(
        _ uiViewController: MFMailComposeViewController,
        context: Context
    ) {}

    final class Coordinator: NSObject, MFMailComposeViewControllerDelegate {
        let onFinish: (String) -> Void

        init(onFinish: @escaping (String) -> Void) {
            self.onFinish = onFinish
        }

        func mailComposeController(
            _ controller: MFMailComposeViewController,
            didFinishWith result: MFMailComposeResult,
            error: Error?
        ) {
            let status = switch result {
            case .sent: "邮件已交给 Mail 发送"
            case .saved: "邮件草稿已保存"
            case .failed: error?.localizedDescription ?? "邮件发送失败"
            case .cancelled: "已取消邮件"
            @unknown default: "邮件编辑已结束"
            }
            controller.dismiss(animated: true)
            onFinish(status)
        }
    }
}

@MainActor
@Observable
final class SpeechInput {
    private(set) var isListening = false
    private(set) var errorMessage: String?

    @ObservationIgnored private let audioEngine = AVAudioEngine()
    @ObservationIgnored private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var recognitionTask: SFSpeechRecognitionTask?
    @ObservationIgnored private var hasAudioTap = false

    func start(onTranscript: @escaping @MainActor (String) -> Void) async {
        guard !isListening else { return }
        errorMessage = nil

        do {
            guard await speechPermission() == .authorized else {
                throw SpeechInputError.speechPermissionDenied
            }
            guard await microphonePermission() else {
                throw SpeechInputError.microphonePermissionDenied
            }
            guard let recognizer = SFSpeechRecognizer(
                locale: Locale(identifier: "zh-CN")
            ), recognizer.isAvailable else {
                throw SpeechInputError.recognizerUnavailable
            }

            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement)
            try session.setActive(true, options: .notifyOthersOnDeactivation)

            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.taskHint = .dictation
            request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition

            let input = audioEngine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                throw SpeechInputError.microphoneUnavailable
            }

            recognitionRequest = request
            recognitionTask = recognizer.recognitionTask(with: request) {
                [weak self] result, error in
                Task { @MainActor in
                    guard let self else { return }
                    if let result {
                        onTranscript(result.bestTranscription.formattedString)
                    }
                    if result?.isFinal == true || error != nil {
                        if self.isListening, result == nil, let error {
                            self.errorMessage = error.localizedDescription
                        }
                        self.stop()
                    }
                }
            }

            input.installTap(
                onBus: 0,
                bufferSize: 1_024,
                format: format
            ) { buffer, _ in
                request.append(buffer)
            }
            hasAudioTap = true
            audioEngine.prepare()
            try audioEngine.start()
            isListening = true
        } catch {
            stop()
            errorMessage = error.localizedDescription
        }
    }

    func stop() {
        if audioEngine.isRunning {
            audioEngine.stop()
        }
        if hasAudioTap {
            audioEngine.inputNode.removeTap(onBus: 0)
            hasAudioTap = false
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionRequest = nil
        recognitionTask = nil
        isListening = false
        try? AVAudioSession.sharedInstance().setActive(
            false,
            options: .notifyOthersOnDeactivation
        )
    }

    private func speechPermission() async -> SFSpeechRecognizerAuthorizationStatus {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization {
                continuation.resume(returning: $0)
            }
        }
    }

    private func microphonePermission() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission {
                continuation.resume(returning: $0)
            }
        }
    }
}

private enum SpeechInputError: LocalizedError {
    case speechPermissionDenied
    case microphonePermissionDenied
    case recognizerUnavailable
    case microphoneUnavailable

    var errorDescription: String? {
        switch self {
        case .speechPermissionDenied:
            "请在系统设置中允许 Wellphone 使用语音识别。"
        case .microphonePermissionDenied:
            "请在系统设置中允许 Wellphone 使用麦克风。"
        case .recognizerUnavailable:
            "语音识别当前不可用，请稍后重试。"
        case .microphoneUnavailable:
            "没有可用的麦克风输入。"
        }
    }
}

#Preview {
    ContentView(coordinator: JobCoordinator())
}
