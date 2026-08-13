import MessageUI
import SwiftUI

struct MailComposer: UIViewControllerRepresentable {
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
