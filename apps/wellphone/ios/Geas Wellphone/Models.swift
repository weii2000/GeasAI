import Foundation
import ImageIO
import CoreGraphics

enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    var string: String? {
        guard case .string(let value) = self else { return nil }
        return value
    }

    var strings: [String]? {
        guard case .array(let values) = self else { return nil }
        let strings = values.compactMap(\.string)
        return strings.count == values.count ? strings : nil
    }

    var bool: Bool? {
        guard case .bool(let value) = self else { return nil }
        return value
    }

    var number: Double? {
        guard case .number(let value) = self else { return nil }
        return value
    }
}

struct ServerTask: Codable, Sendable {
    enum Status: String, Codable, Sendable {
        case running
        case waitingForPhone = "waiting_for_phone"
        case completed
        case failed
        case cancelled
    }

    let id: String
    let sessionID: String
    let prompt: String
    let status: Status
    let answer: String?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case id
        case sessionID = "session_id"
        case prompt
        case status
        case answer
        case error
    }
}

struct ConversationMessage: Codable, Sendable, Identifiable {
    enum Role: String, Codable, Sendable {
        case user
        case assistant
    }

    let id: String
    let role: Role
    let content: String
    let timestamp: String
}

struct TaskActivity: Identifiable, Sendable {
    enum State: Sendable, Equatable {
        case running
        case completed
        case failed
        case cancelled
    }

    let id: String
    var title: String
    var detail: String? = nil
    var state: State
}

struct ServerSession: Codable, Sendable {
    let id: String
    let createdAt: String
    let updatedAt: String
    let messages: [ConversationMessage]

    enum CodingKeys: String, CodingKey {
        case id
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case messages
    }
}

struct ToolCall: Codable, Sendable {
    let taskID: String
    let callID: String
    let name: String
    let arguments: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case callID = "call_id"
        case name
        case arguments
    }
}

struct ToolPoll: Codable, Sendable {
    let toolCall: ToolCall?

    enum CodingKeys: String, CodingKey {
        case toolCall = "tool_call"
    }
}

struct ToolResultRequest: Codable, Sendable {
    let callID: String
    let result: [String: JSONValue]
    let isError: Bool

    enum CodingKeys: String, CodingKey {
        case callID = "call_id"
        case result
        case isError = "is_error"
    }
}

struct PhotoSummary: Sendable {
    let identifier: String
    let createdAt: String?
    let width: Int
    let height: Int
    let isFavorite: Bool
    let isHidden: Bool
    let isScreenshot: Bool
    let mediaType: String
    let duration: Double
    let addedAt: String?
    let latitude: Double?
    let longitude: Double?

    var json: JSONValue {
        .object([
            "identifier": .string(identifier),
            "created_at": createdAt.map(JSONValue.string) ?? .null,
            "width": .number(Double(width)),
            "height": .number(Double(height)),
            "is_favorite": .bool(isFavorite),
            "is_hidden": .bool(isHidden),
            "is_screenshot": .bool(isScreenshot),
            "media_type": .string(mediaType),
            "duration": .number(duration),
            "added_at": addedAt.map(JSONValue.string) ?? .null,
            "latitude": latitude.map(JSONValue.number) ?? .null,
            "longitude": longitude.map(JSONValue.number) ?? .null,
        ])
    }
}

struct AlbumSummary: Sendable {
    let identifier: String
    let name: String
    let count: Int

    var json: JSONValue {
        .object([
            "album_id": .string(identifier),
            "name": .string(name),
            "count": .number(Double(count)),
        ])
    }
}

struct MailDraft: Codable, Identifiable, Sendable {
    let id: String
    let to: [String]
    let cc: [String]
    let bcc: [String]
    let subject: String
    let body: String
}

struct PendingAction: Codable, Identifiable, Sendable {
    enum Kind: String, Codable, Sendable {
        case mail
        case url
    }

    let id: String
    let kind: Kind
    let title: String
    let detail: String
    let buttonTitle: String
    let url: URL?
    let mailDraft: MailDraft?
}

enum WellphoneNotification {
    static let categoryID = "WELLPHONE_PENDING_ACTION"
    static let openActionID = "OPEN_PENDING_ACTION"
    static let actionIDKey = "action_id"
    static let selected = Notification.Name("WellphoneNotificationSelected")
    static let selectedActionKey = "wellphone.selectedNotificationAction"
}

struct ToolApproval: Identifiable, Sendable {
    let id = UUID()
    let title: String
    let message: String
    let destructive: Bool
}

struct PhotoImage: @unchecked Sendable {
    let image: CGImage
    let orientation: CGImagePropertyOrientation
}

enum WellphoneError: LocalizedError {
    case invalidServerURL
    case invalidArguments(String)
    case fullPhotoAccessRequired
    case missingPhoto(String)
    case missingAlbum(String)
    case albumNotWritable(String)
    case toolScopeViolation(String)
    case server(String)
    case backgroundRegistrationFailed
    case backgroundStartTimedOut
    case mailUnavailable
    case externalAppUnavailable
    case userDeclined(String)

    var errorDescription: String? {
        switch self {
        case .invalidServerURL:
            "请输入 Mac 的有效 HTTP 地址，例如 http://192.168.1.10:8000"
        case .invalidArguments(let message):
            "工具参数错误：\(message)"
        case .fullPhotoAccessRequired:
            "整理相册需要照片的 Full Access；Limited Access 无法查询或创建用户相册。"
        case .missingPhoto(let identifier):
            "找不到照片：\(identifier)"
        case .missingAlbum(let identifier):
            "找不到相册：\(identifier)"
        case .albumNotWritable(let identifier):
            "相册不可写：\(identifier)"
        case .toolScopeViolation(let message):
            "已阻止超出本次任务范围的操作：\(message)"
        case .server(let message):
            "Server 错误：\(message)"
        case .backgroundRegistrationFailed:
            "后台任务注册失败，请检查 Bundle ID 和 Info.plist。"
        case .backgroundStartTimedOut:
            "系统未能及时启动后台任务，请稍后重试。"
        case .mailUnavailable:
            "这台设备尚未在 Apple Mail 中配置可发送邮件的账户。"
        case .externalAppUnavailable:
            "无法打开外部 App 或网页。"
        case .userDeclined(let action):
            "用户未批准操作：\(action)"
        }
    }
}

extension Dictionary where Key == String, Value == JSONValue {
    func requiredString(_ key: String) throws -> String {
        guard let value = self[key]?.string, !value.isEmpty else {
            throw WellphoneError.invalidArguments("\(key) 必须是非空字符串")
        }
        return value
    }

    func requiredStrings(_ key: String) throws -> [String] {
        guard let values = self[key]?.strings, !values.isEmpty else {
            throw WellphoneError.invalidArguments("\(key) 必须是非空字符串数组")
        }
        return values
    }

    func optionalStrings(_ key: String) throws -> [String] {
        guard let value = self[key] else { return [] }
        guard let values = value.strings else {
            throw WellphoneError.invalidArguments("\(key) 必须是字符串数组")
        }
        return values
    }

    func requiredBool(_ key: String) throws -> Bool {
        guard let value = self[key]?.bool else {
            throw WellphoneError.invalidArguments("\(key) 必须是布尔值")
        }
        return value
    }

    func optionalBool(_ key: String) throws -> Bool? {
        guard let raw = self[key] else { return nil }
        guard let value = raw.bool else {
            throw WellphoneError.invalidArguments("\(key) 必须是布尔值")
        }
        return value
    }

    func requiredNumber(_ key: String) throws -> Double {
        guard let value = self[key]?.number else {
            throw WellphoneError.invalidArguments("\(key) 必须是数字")
        }
        return value
    }
}
