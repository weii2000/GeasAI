import Foundation
import ImageIO

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
}

struct ServerTask: Codable, Sendable {
    enum Status: String, Codable, Sendable {
        case running
        case waitingForPhone = "waiting_for_phone"
        case completed
        case failed
    }

    let id: String
    let prompt: String
    let status: Status
    let answer: String?
    let error: String?
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

    var json: JSONValue {
        .object([
            "identifier": .string(identifier),
            "created_at": createdAt.map(JSONValue.string) ?? .null,
            "width": .number(Double(width)),
            "height": .number(Double(height)),
            "is_favorite": .bool(isFavorite),
        ])
    }
}

struct PhotoImage: @unchecked Sendable {
    let data: Data
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
}
