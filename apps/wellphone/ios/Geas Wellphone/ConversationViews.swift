import SwiftUI

struct PendingActionCard: View {
    let action: PendingAction
    let onOpen: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: action.kind == .mail ? "envelope.fill" : "arrow.up.forward.app.fill")
                    .foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 3) {
                    Text(action.title)
                        .font(.subheadline.weight(.semibold))
                    Text(action.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                Spacer()
                Button("忽略", systemImage: "xmark", action: onDismiss)
                    .labelStyle(.iconOnly)
                    .foregroundStyle(.secondary)
            }

            Button(action.buttonTitle, action: onOpen)
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(
            Color(.secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 16)
        )
        .padding(.horizontal)
    }
}

struct SettingsView: View {
    @Binding var serverAddress: String
    @FocusState private var addressFocused: Bool

    var body: some View {
        Form {
            Section {
                TextField("http://192.168.1.10:8000", text: $serverAddress)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    .autocorrectionDisabled()
                    .focused($addressFocused)
                    .submitLabel(.done)
                    .onSubmit { addressFocused = false }
            } header: {
                Text("Agent Server")
            } footer: {
                Text("填写 Mac 上 Wellphone Server 的局域网地址。")
            }
        }
        .navigationTitle("设置")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct MessageBubble: View {
    let message: ConversationMessage

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 52) }
            Text(renderedContent)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .foregroundStyle(message.role == .user ? .white : .primary)
                .multilineTextAlignment(.leading)
                .textSelection(.enabled)
                .background(
                    message.role == .user ? Color.accentColor : Color(.secondarySystemGroupedBackground),
                    in: RoundedRectangle(cornerRadius: 18)
                )
            if message.role == .assistant { Spacer(minLength: 52) }
        }
        .padding(.horizontal)
    }

    private var renderedContent: AttributedString {
        guard message.role == .assistant else {
            return AttributedString(message.content)
        }
        return (try? AttributedString(
            markdown: message.content,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(message.content)
    }
}

struct TaskActivityCard: View {
    let status: String
    let activities: [TaskActivity]
    let error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                if error == nil {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    Image(systemName: "exclamationmark.circle.fill")
                        .foregroundStyle(.red)
                }
                Text(error == nil ? status : "出现问题")
                    .font(.subheadline.weight(.medium))
            }

            ForEach(activities) { activity in
                HStack(alignment: .top, spacing: 8) {
                    activityIcon(activity.state)
                        .frame(width: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(activity.title)
                            .font(.callout)
                        if let detail = activity.detail {
                            Text(detail)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            if let error {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(
            Color(.secondarySystemGroupedBackground),
            in: RoundedRectangle(cornerRadius: 16)
        )
        .padding(.horizontal)
    }

    @ViewBuilder
    private func activityIcon(_ state: TaskActivity.State) -> some View {
        switch state {
        case .running:
            ProgressView()
                .controlSize(.mini)
        case .completed:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed:
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.red)
        case .cancelled:
            Image(systemName: "minus.circle.fill")
                .foregroundStyle(.secondary)
        }
    }
}
