import AVFoundation
import Observation
import Speech
import SwiftUI

struct ContentView: View {
    @Bindable var coordinator: JobCoordinator
    @State private var prompt = ""
    @State private var speech = SpeechInput()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Wellphone")
                            .font(.largeTitle.bold())
                        Text("让 Agent 在后台整理照片")
                            .foregroundStyle(.secondary)
                    }
                    .padding(.bottom, 4)

                    VStack(alignment: .leading, spacing: 12) {
                        Label("连接到 Mac", systemImage: "desktopcomputer")
                            .font(.headline)

                        TextField(
                            "http://192.168.1.10:8000",
                            text: $coordinator.serverAddress
                        )
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)
                        .disabled(coordinator.isRunning)
                    }
                    .padding(18)
                    .background(.background, in: RoundedRectangle(cornerRadius: 20))

                    VStack(alignment: .leading, spacing: 12) {
                        Label("任务", systemImage: "sparkles")
                            .font(.headline)

                        ZStack(alignment: .topLeading) {
                            if prompt.isEmpty {
                                Text("例如：把今天包含文档的照片整理到工作相册")
                                    .foregroundStyle(.tertiary)
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 8)
                            }
                            TextEditor(text: $prompt)
                                .scrollContentBackground(.hidden)
                                .frame(minHeight: 130)
                                .disabled(coordinator.isRunning)
                        }
                        .padding(8)
                        .background(
                            Color(.tertiarySystemGroupedBackground),
                            in: RoundedRectangle(cornerRadius: 14)
                        )

                        HStack {
                            Button {
                                if speech.isListening {
                                    speech.stop()
                                } else {
                                    Task {
                                        await speech.start { prompt = $0 }
                                    }
                                }
                            } label: {
                                Label(
                                    speech.isListening ? "停止听写" : "语音输入",
                                    systemImage: speech.isListening
                                        ? "stop.circle.fill"
                                        : "mic.fill"
                                )
                            }
                            .buttonStyle(.bordered)
                            .tint(speech.isListening ? .red : .accentColor)
                            .disabled(coordinator.isRunning)

                            Spacer()

                            if speech.isListening {
                                Text("正在听…")
                                    .font(.caption)
                                    .foregroundStyle(.red)
                            }
                        }

                        if let error = speech.errorMessage {
                            Text(error)
                                .font(.caption)
                                .foregroundStyle(.red)
                        }

                        if coordinator.isRunning {
                            Button("取消任务", role: .destructive) {
                                coordinator.cancel()
                            }
                            .buttonStyle(.bordered)
                            .frame(maxWidth: .infinity)
                        } else {
                            Button {
                                speech.stop()
                                coordinator.start(prompt: prompt)
                            } label: {
                                Label("开始后台整理", systemImage: "play.fill")
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                            .disabled(
                                prompt.trimmingCharacters(
                                    in: .whitespacesAndNewlines
                                ).isEmpty
                            )
                        }
                    }
                    .padding(18)
                    .background(.background, in: RoundedRectangle(cornerRadius: 20))

                    VStack(alignment: .leading, spacing: 10) {
                        Label("状态", systemImage: "waveform.path.ecg")
                            .font(.headline)

                        HStack(spacing: 10) {
                            if coordinator.isRunning {
                                ProgressView()
                            }
                            Text(coordinator.status)
                        }

                        if !coordinator.answer.isEmpty {
                            Divider()
                            Text(coordinator.answer)
                                .foregroundStyle(.secondary)
                        }

                        if let error = coordinator.errorMessage {
                            Text(error)
                                .font(.callout)
                                .foregroundStyle(.red)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .background(.background, in: RoundedRectangle(cornerRadius: 20))

                    Label(
                        "运行后可以切换到其他 App；请勿强制关闭 Wellphone。",
                        systemImage: "iphone.and.arrow.forward"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 4)
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .onDisappear { speech.stop() }
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
