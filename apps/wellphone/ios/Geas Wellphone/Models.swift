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

    var object: [String: JSONValue]? {
        guard case .object(let value) = self else { return nil }
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

enum ToolName: String, Sendable {
    case searchPhotos = "search_photos"
    case getPhotoDetails = "get_photo_details"
    case analyzePhotos = "analyze_photos"
    case listAlbums = "list_albums"
    case findAlbum = "find_album"
    case createAlbum = "create_album"
    case renameAlbum = "rename_album"
    case deleteAlbum = "delete_album"
    case addPhotosToAlbum = "add_photos_to_album"
    case removePhotosFromAlbum = "remove_photos_from_album"
    case getAlbumContents = "get_album_contents"
    case setFavorite = "set_favorite"
    case setHidden = "set_hidden"
    case setPhotoCreationDate = "set_photo_creation_date"
    case setPhotoLocation = "set_photo_location"
    case deletePhotos = "delete_photos"
    case composeEmail = "compose_email"
    case openYouTubeVideo = "open_youtube_video"
    case openGoogleMapsSearch = "open_google_maps_search"
    case openGoogleMapsDirections = "open_google_maps_directions"
    case getCurrentLocation = "get_current_location"
    case geocodeAddress = "geocode_address"
    case reverseGeocodeLocation = "reverse_geocode_location"
    case searchNearbyPlaces = "search_nearby_places"
    case getHealthSummary = "get_health_summary"
    case getSleepSummary = "get_sleep_summary"
    case listHealthWorkouts = "list_health_workouts"
    case listScheduledWorkouts = "list_scheduled_workouts"
    case scheduleWorkout = "schedule_workout"
    case removeScheduledWorkout = "remove_scheduled_workout"
    case searchContacts = "search_contacts"

    var requiresPhotoAccess: Bool {
        switch self {
        case .composeEmail, .openYouTubeVideo,
             .openGoogleMapsSearch, .openGoogleMapsDirections,
             .getCurrentLocation, .geocodeAddress,
             .reverseGeocodeLocation, .searchNearbyPlaces,
             .getHealthSummary, .getSleepSummary, .listHealthWorkouts,
             .listScheduledWorkouts, .scheduleWorkout,
             .removeScheduledWorkout, .searchContacts:
            false
        case .searchPhotos, .getPhotoDetails, .analyzePhotos, .listAlbums,
             .findAlbum, .createAlbum, .renameAlbum, .deleteAlbum,
             .addPhotosToAlbum, .removePhotosFromAlbum, .getAlbumContents,
             .setFavorite, .setHidden, .setPhotoCreationDate,
             .setPhotoLocation, .deletePhotos:
            true
        }
    }

    var displayName: String {
        switch self {
        case .searchPhotos: "查找照片"
        case .getPhotoDetails: "读取照片信息"
        case .analyzePhotos: "设备端识别照片"
        case .listAlbums: "读取相册"
        case .findAlbum: "查找相册"
        case .createAlbum: "创建相册"
        case .renameAlbum: "重命名相册"
        case .deleteAlbum: "删除相册"
        case .addPhotosToAlbum: "加入相册"
        case .removePhotosFromAlbum: "移出相册"
        case .getAlbumContents: "核对相册"
        case .setFavorite: "修改收藏"
        case .setHidden: "修改隐藏状态"
        case .setPhotoCreationDate: "修改照片日期"
        case .setPhotoLocation: "修改照片位置"
        case .deletePhotos: "删除照片"
        case .composeEmail: "准备邮件草稿"
        case .openYouTubeVideo: "准备 YouTube 视频"
        case .openGoogleMapsSearch: "准备地图搜索"
        case .openGoogleMapsDirections: "准备路线规划"
        case .getCurrentLocation: "读取当前位置"
        case .geocodeAddress: "解析地址"
        case .reverseGeocodeLocation: "解析坐标"
        case .searchNearbyPlaces: "搜索附近地点"
        case .getHealthSummary: "汇总活动数据"
        case .getSleepSummary: "汇总睡眠数据"
        case .listHealthWorkouts: "读取运动历史"
        case .listScheduledWorkouts: "读取训练计划"
        case .scheduleWorkout: "安排训练"
        case .removeScheduledWorkout: "移除训练计划"
        case .searchContacts: "查找联系人"
        }
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
    let isHTML: Bool
    let attachmentPhotoIDs: [String]

    enum CodingKeys: String, CodingKey {
        case id, to, cc, bcc, subject, body, isHTML, attachmentPhotoIDs
    }

    init(
        id: String,
        to: [String],
        cc: [String],
        bcc: [String],
        subject: String,
        body: String,
        isHTML: Bool = false,
        attachmentPhotoIDs: [String] = []
    ) {
        self.id = id
        self.to = to
        self.cc = cc
        self.bcc = bcc
        self.subject = subject
        self.body = body
        self.isHTML = isHTML
        self.attachmentPhotoIDs = attachmentPhotoIDs
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        to = try values.decode([String].self, forKey: .to)
        cc = try values.decode([String].self, forKey: .cc)
        bcc = try values.decode([String].self, forKey: .bcc)
        subject = try values.decode(String.self, forKey: .subject)
        body = try values.decode(String.self, forKey: .body)
        isHTML = try values.decodeIfPresent(Bool.self, forKey: .isHTML) ?? false
        attachmentPhotoIDs = try values.decodeIfPresent(
            [String].self,
            forKey: .attachmentPhotoIDs
        ) ?? []
    }
}

struct MailAttachment: Sendable {
    let data: Data
    let mimeType: String
    let filename: String
}

struct MailPresentation: Identifiable, Sendable {
    var id: String { draft.id }
    let draft: MailDraft
    let attachments: [MailAttachment]
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
    case permissionRequired(String)
    case healthUnavailable
    case workoutUnavailable(String)
    case attachmentLimit

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
        case .permissionRequired(let name):
            "请先在 Wellphone 设置页授权\(name)。"
        case .healthUnavailable:
            "这台设备不支持 HealthKit。"
        case .workoutUnavailable(let reason):
            "WorkoutKit 不可用：\(reason)"
        case .attachmentLimit:
            "邮件最多附加 3 张照片，压缩后总大小不能超过 15 MB。"
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

    func optionalNumber(_ key: String) throws -> Double? {
        guard let raw = self[key] else { return nil }
        guard let value = raw.number else {
            throw WellphoneError.invalidArguments("\(key) 必须是数字")
        }
        return value
    }

    func requiredInteger(_ key: String) throws -> Int {
        let value = try requiredNumber(key)
        guard value.rounded() == value,
              value >= Double(Int.min), value <= Double(Int.max) else {
            throw WellphoneError.invalidArguments("\(key) 必须是整数")
        }
        return Int(value)
    }
}
