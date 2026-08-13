import Combine
import Foundation
import Observation
import SwiftUI

struct ContentView: View {
    @Bindable var coordinator: JobCoordinator
    @State private var prompt = ""
    @State private var speech = SpeechInput()
    @FocusState private var composerFocused: Bool
    @Environment(\.scenePhase) private var scenePhase

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

                            if coordinator.isRunning || coordinator.errorMessage != nil {
                                TaskActivityCard(
                                    status: coordinator.status,
                                    activities: coordinator.activities,
                                    error: coordinator.errorMessage
                                )
                            }

                            ForEach(coordinator.pendingActions) { action in
                                PendingActionCard(
                                    action: action,
                                    onOpen: {
                                        Task {
                                            await coordinator.performPendingAction(id: action.id)
                                        }
                                    },
                                    onDismiss: {
                                        coordinator.dismissPendingAction(id: action.id)
                                    }
                                )
                            }

                            Color.clear
                                .frame(height: 1)
                                .id("conversation-bottom")
                        }
                        .padding(.vertical)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .simultaneousGesture(
                        TapGesture().onEnded { composerFocused = false }
                    )
                    .onChange(
                        of: coordinator.messages.count
                            + coordinator.activities.count
                            + coordinator.pendingActions.count
                    ) {
                        withAnimation {
                            proxy.scrollTo("conversation-bottom", anchor: .bottom)
                        }
                    }
                }

                Divider()

                VStack(spacing: 6) {
                    HStack(alignment: .bottom, spacing: 8) {
                        TextField("给 Wellphone 发消息…", text: $prompt, axis: .vertical)
                            .lineLimit(1...5)
                            .textFieldStyle(.plain)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 10)
                            .background(
                                Color(.secondarySystemBackground),
                                in: RoundedRectangle(cornerRadius: 20)
                            )
                            .focused($composerFocused)
                            .disabled(coordinator.isRunning)

                        Button(action: toggleSpeech) {
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
                            Button(action: sendPrompt) {
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
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        SettingsView(serverAddress: $coordinator.serverAddress)
                    } label: {
                        Label("设置", systemImage: "gearshape")
                    }
                    .disabled(coordinator.isRunning)
                }
            }
            .task {
                await coordinator.consumeSelectedNotification()
                await coordinator.restoreSession()
            }
            .onChange(of: scenePhase) {
                guard scenePhase == .active else { return }
                Task { await coordinator.consumeSelectedNotification() }
            }
            .onReceive(
                NotificationCenter.default.publisher(for: WellphoneNotification.selected)
            ) { _ in
                guard scenePhase == .active else { return }
                Task { await coordinator.consumeSelectedNotification() }
            }
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

    private func toggleSpeech() {
        if speech.isListening {
            speech.stop()
            return
        }

        composerFocused = false
        let prefix = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            await speech.start { transcript in
                prompt = prefix.isEmpty ? transcript : "\(prefix) \(transcript)"
            }
        }
    }

    private func sendPrompt() {
        let text = prompt
        prompt = ""
        composerFocused = false
        speech.stop()
        coordinator.start(prompt: text)
    }
}

#Preview {
    ContentView(coordinator: JobCoordinator())
}
