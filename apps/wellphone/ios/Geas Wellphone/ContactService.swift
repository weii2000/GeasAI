@preconcurrency import Contacts
import Foundation

@MainActor
final class ContactService {
    private let store = CNContactStore()

    func search(query: String, limit: Int) throws -> [JSONValue] {
        guard CNContactStore.authorizationStatus(for: .contacts) == .authorized else {
            throw WellphoneError.permissionRequired("联系人")
        }
        guard (1...10).contains(limit) else {
            throw WellphoneError.invalidArguments("联系人数量必须在 1 到 10 之间")
        }
        let keys: [CNKeyDescriptor] = [
            CNContactFormatter.descriptorForRequiredKeys(for: .fullName),
            CNContactEmailAddressesKey as CNKeyDescriptor,
        ]
        return try store.unifiedContacts(
            matching: CNContact.predicateForContacts(matchingName: query),
            keysToFetch: keys
        )
        .lazy
        .filter { !$0.emailAddresses.isEmpty }
        .prefix(limit)
        .map { contact in
            .object([
                "name": .string(
                    CNContactFormatter.string(from: contact, style: .fullName)
                        ?? "未命名联系人"
                ),
                "emails": .array(contact.emailAddresses.map {
                    .object([
                        "label": CNLabeledValue<NSString>.localizedString(
                            forLabel: $0.label ?? ""
                        ).isEmpty ? .null : .string(
                            CNLabeledValue<NSString>.localizedString(
                                forLabel: $0.label ?? ""
                            )
                        ),
                        "address": .string($0.value as String),
                    ])
                }),
            ])
        }
    }
}
