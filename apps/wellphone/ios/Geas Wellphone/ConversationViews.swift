import SwiftUI
import UIKit

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
    @State private var permissions = PermissionCenter()

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

            Section {
                PermissionRow(name: "照片", status: permissions.photoStatus) {
                    Task { await permissions.requestPhotos() }
                }
                PermissionRow(name: "位置", status: permissions.locationStatus) {
                    permissions.requestLocation()
                }
                PermissionRow(name: "健康", status: permissions.healthStatus) {
                    Task { await permissions.requestHealth() }
                }
                PermissionRow(name: "联系人", status: permissions.contactStatus) {
                    Task { await permissions.requestContacts() }
                }
                PermissionRow(name: "训练计划", status: permissions.workoutStatus) {
                    Task { await permissions.requestWorkout() }
                }
                PermissionRow(name: "通知", status: permissions.notificationStatus) {
                    Task { await permissions.requestNotifications() }
                }
                Button("打开系统设置", systemImage: "gear") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else {
                        return
                    }
                    UIApplication.shared.open(url)
                }
            } header: {
                Text("设备权限")
            } footer: {
                Text("可在这里提前授权；相关功能首次使用时也可能请求。拒绝后可在系统设置中修改。")
            }

            if let error = permissions.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                }
            }
        }
        .navigationTitle("设置")
        .navigationBarTitleDisplayMode(.inline)
        .task { await permissions.refresh() }
    }
}

private struct PermissionRow: View {
    let name: String
    let status: String
    let request: () -> Void

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(name)
                Text(status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("授权", action: request)
                .buttonStyle(.bordered)
                .controlSize(.small)
        }
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
