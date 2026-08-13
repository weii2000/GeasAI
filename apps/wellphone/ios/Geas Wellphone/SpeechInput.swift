import AVFoundation
import Observation
import Speech

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
                    guard let self, self.recognitionRequest === request else { return }
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
