import Foundation
import MessageUI
import UIKit

@MainActor
final class ToolExecutor {
    private let photos = PhotoService()
    private let ocr = OCRService()
    private var allowedPhotoIDs: Set<String> = []
    private var writableAlbumIDs: Set<String> = []
    private var initialSearchRange: (start: Date, end: Date)?
    private var albumName: String?
    private var scopeLocked = false
    private let photoToolNames: Set<String> = [
        "search_photos", "get_photo_details", "analyze_photos", "list_albums",
        "find_album", "create_album", "rename_album", "delete_album",
        "add_photos_to_album", "remove_photos_from_album", "get_album_contents",
        "set_favorite", "set_hidden", "set_photo_creation_date",
        "set_photo_location", "delete_photos",
    ]

    func resetScope() {
        allowedPhotoIDs.removeAll()
        writableAlbumIDs.removeAll()
        initialSearchRange = nil
        albumName = nil
        scopeLocked = false
    }

    func execute(
        _ call: ToolCall,
        onProgress: (String) -> Void = { _ in },
        approve: (ToolApproval) async -> Bool = { _ in false },
        onMailDraft: (MailDraft) -> Void = { _ in }
    ) async -> ToolResultRequest {
        do {
            if photoToolNames.contains(call.name) {
                try await photos.requireFullAccess()
            }
            let result = try await execute(
                name: call.name,
                arguments: call.arguments,
                onProgress: onProgress,
                approve: approve,
                onMailDraft: onMailDraft
            )
            return ToolResultRequest(callID: call.callID, result: result, isError: false)
        } catch {
            return ToolResultRequest(
                callID: call.callID,
                result: ["message": .string(error.localizedDescription)],
                isError: true
            )
        }
    }

    private func execute(
        name: String,
        arguments: [String: JSONValue],
        onProgress: (String) -> Void,
        approve: (ToolApproval) async -> Bool,
        onMailDraft: (MailDraft) -> Void
    ) async throws -> [String: JSONValue] {
        switch name {
        case "search_photos":
            let start = try parseDate(arguments.requiredString("start"))
            let end = try parseDate(arguments.requiredString("end"))
            guard !scopeLocked else {
                throw WellphoneError.toolScopeViolation("照片分析或修改后不能继续搜索")
            }
            if let initialSearchRange {
                guard start >= initialSearchRange.start,
                      end <= initialSearchRange.end else {
                    throw WellphoneError.toolScopeViolation("后续搜索必须位于首次搜索区间内")
                }
            }
            let results = try photos.search(
                start: start,
                end: end,
                mediaType: try arguments.requiredString("media_type"),
                includeScreenshots: try arguments.requiredBool("include_screenshots"),
                favorite: try arguments.optionalBool("favorite"),
                hidden: try arguments.optionalBool("hidden")
            )
            if initialSearchRange == nil {
                initialSearchRange = (start, end)
            }
            allowedPhotoIDs.formUnion(results.map(\.identifier))
            return [
                "count": .number(Double(results.count)),
                "truncated": .bool(results.count == 200),
                "photos": .array(results.map(\.json)),
            ]

        case "get_photo_details":
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireAllowed(identifiers)
            let result = photos.details(identifiers: identifiers)
            return [
                "photos": .array(result.photos.map(\.json)),
                "missing_identifiers": strings(result.missing),
            ]

        case "analyze_photos":
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireAllowed(identifiers)
            guard identifiers.count <= 12 else {
                throw WellphoneError.invalidArguments("每批最多分析 12 张照片")
            }
            scopeLocked = true
            var results: [JSONValue] = []
            for (index, identifier) in identifiers.enumerated() {
                try Task.checkCancellation()
                onProgress("OCR \(index + 1)/\(identifiers.count)")
                do {
                    let image = try await photos.image(identifier: identifier)
                    let text = try await ocr.recognize(image)
                    results.append(.object([
                        "identifier": .string(identifier),
                        "text": .string(text),
                    ]))
                } catch is CancellationError {
                    throw CancellationError()
                } catch {
                    results.append(.object([
                        "identifier": .string(identifier),
                        "error": .string(error.localizedDescription),
                    ]))
                }
            }
            return ["analyses": .array(results)]

        case "list_albums":
            let albums = photos.listAlbums()
            return [
                "count": .number(Double(albums.count)),
                "albums": .array(albums.map(\.json)),
            ]

        case "find_album":
            let name = try arguments.requiredString("name")
            guard !scopeLocked || albumName != nil else {
                throw WellphoneError.toolScopeViolation("照片分析后不能再选择目标相册")
            }
            let identifier = try photos.findAlbumID(named: name)
            try selectAlbum(identifier: identifier, name: name)
            return ["album_id": .string(identifier), "name": .string(name)]

        case "create_album":
            let name = try arguments.requiredString("name")
            guard !scopeLocked || albumName != nil else {
                throw WellphoneError.toolScopeViolation("照片分析后不能再选择目标相册")
            }
            if let albumName, albumName != name {
                throw WellphoneError.toolScopeViolation(
                    "一次运行只能操作一个目标相册：\(albumName)"
                )
            }
            let identifier = try await photos.findOrCreateAlbum(named: name)
            try selectAlbum(identifier: identifier, name: name)
            return ["album_id": .string(identifier), "name": .string(name)]

        case "rename_album":
            let albumID = try arguments.requiredString("album_id")
            let newName = try arguments.requiredString("new_name")
            try requireWritableAlbum(albumID)
            try await requireApproval(
                ToolApproval(
                    title: "重命名相册？",
                    message: "将“\(albumName ?? "相册")”改名为“\(newName)”。",
                    destructive: false
                ),
                approve
            )
            try await photos.renameAlbum(identifier: albumID, to: newName)
            albumName = newName
            return ["album_id": .string(albumID), "name": .string(newName)]

        case "delete_album":
            let albumID = try arguments.requiredString("album_id")
            try requireWritableAlbum(albumID)
            try await requireApproval(
                ToolApproval(
                    title: "删除相册？",
                    message: "只删除相册“\(albumName ?? "")”，其中照片仍保留在照片图库。",
                    destructive: true
                ),
                approve
            )
            try await photos.deleteAlbum(identifier: albumID)
            writableAlbumIDs.remove(albumID)
            return ["deleted": .bool(true)]

        case "add_photos_to_album":
            let albumID = try arguments.requiredString("album_id")
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireWritableAlbum(albumID)
            try requireAllowed(identifiers)
            scopeLocked = true
            let result = try await photos.addPhotos(identifiers: identifiers, toAlbum: albumID)
            return [
                "added_count": .number(Double(result.added)),
                "missing_identifiers": strings(result.missing),
            ]

        case "remove_photos_from_album":
            let albumID = try arguments.requiredString("album_id")
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireWritableAlbum(albumID)
            try requireAllowed(identifiers)
            try await requireApproval(
                ToolApproval(
                    title: "从相册移除照片？",
                    message: "从“\(albumName ?? "相册")”移除 \(identifiers.count) 项，不会删除原照片。",
                    destructive: true
                ),
                approve
            )
            scopeLocked = true
            let result = try await photos.removePhotos(
                identifiers: identifiers,
                fromAlbum: albumID
            )
            return [
                "removed_count": .number(Double(result.removed)),
                "not_present_identifiers": strings(result.notPresent),
            ]

        case "get_album_contents":
            let albumID = try arguments.requiredString("album_id")
            try requireWritableAlbum(albumID)
            let identifiers = try photos.albumContents(identifier: albumID)
            return [
                "count": .number(Double(identifiers.count)),
                "identifiers": strings(identifiers),
            ]

        case "set_favorite":
            let identifiers = try arguments.requiredStrings("identifiers")
            let favorite = try arguments.requiredBool("favorite")
            try requireAllowed(identifiers)
            try await requireApproval(
                ToolApproval(
                    title: favorite ? "标记为收藏？" : "取消收藏？",
                    message: "将修改 \(identifiers.count) 项的收藏状态。",
                    destructive: false
                ),
                approve
            )
            scopeLocked = true
            let result = try await photos.setFavorite(identifiers: identifiers, value: favorite)
            return [
                "updated_count": .number(Double(result.updated)),
                "missing_identifiers": strings(result.missing),
            ]

        case "set_hidden":
            let identifiers = try arguments.requiredStrings("identifiers")
            let hidden = try arguments.requiredBool("hidden")
            try requireAllowed(identifiers)
            try await requireApproval(
                ToolApproval(
                    title: hidden ? "隐藏照片？" : "取消隐藏？",
                    message: "将修改 \(identifiers.count) 项的隐藏状态。",
                    destructive: hidden
                ),
                approve
            )
            scopeLocked = true
            let result = try await photos.setHidden(identifiers: identifiers, value: hidden)
            return [
                "updated_count": .number(Double(result.updated)),
                "missing_identifiers": strings(result.missing),
            ]

        case "set_photo_creation_date":
            let identifier = try arguments.requiredString("identifier")
            let dateString = try arguments.requiredString("date")
            try requireAllowed([identifier])
            let date = try parseDate(dateString)
            try await requireApproval(
                ToolApproval(
                    title: "修改照片日期？",
                    message: "将一项的拍摄日期改为 \(dateString)。",
                    destructive: false
                ),
                approve
            )
            scopeLocked = true
            try await photos.setCreationDate(identifier: identifier, date: date)
            return ["updated": .bool(true)]

        case "set_photo_location":
            let identifiers = try arguments.requiredStrings("identifiers")
            let latitude = try arguments.requiredNumber("latitude")
            let longitude = try arguments.requiredNumber("longitude")
            try requireAllowed(identifiers)
            guard (-90...90).contains(latitude), (-180...180).contains(longitude) else {
                throw WellphoneError.invalidArguments("经纬度超出范围")
            }
            try await requireApproval(
                ToolApproval(
                    title: "修改照片位置？",
                    message: "将 \(identifiers.count) 项的位置改为 \(latitude), \(longitude)。",
                    destructive: false
                ),
                approve
            )
            scopeLocked = true
            let result = try await photos.setLocation(
                identifiers: identifiers,
                latitude: latitude,
                longitude: longitude
            )
            return [
                "updated_count": .number(Double(result.updated)),
                "missing_identifiers": strings(result.missing),
            ]

        case "delete_photos":
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireAllowed(identifiers)
            try await requireApproval(
                ToolApproval(
                    title: "删除照片？",
                    message: "将请求从照片图库删除 \(identifiers.count) 项；iOS 还会进行系统确认。",
                    destructive: true
                ),
                approve
            )
            scopeLocked = true
            let result = try await photos.deletePhotos(identifiers: identifiers)
            allowedPhotoIDs.subtract(identifiers)
            return [
                "deleted_count": .number(Double(result.deleted)),
                "missing_identifiers": strings(result.missing),
            ]

        case "compose_email":
            guard MFMailComposeViewController.canSendMail() else {
                throw WellphoneError.mailUnavailable
            }
            let draft = MailDraft(
                to: try recipients(arguments.requiredStrings("to")),
                cc: try recipients(arguments.optionalStrings("cc")),
                bcc: try recipients(arguments.optionalStrings("bcc")),
                subject: try arguments.requiredString("subject"),
                body: try arguments.requiredString("body")
            )
            onMailDraft(draft)
            return [
                "prepared": .bool(true),
                "requires_user_send": .bool(true),
                "recipient_count": .number(Double(draft.to.count + draft.cc.count + draft.bcc.count)),
            ]

        case "open_youtube_video":
            let videoID = try arguments.requiredString("video_id")
            let title = try arguments.requiredString("title")
            let allowed = CharacterSet.alphanumerics.union(
                CharacterSet(charactersIn: "-_")
            )
            guard videoID.count == 11,
                  videoID.unicodeScalars.allSatisfy(allowed.contains) else {
                throw WellphoneError.invalidArguments("YouTube video_id 格式无效")
            }
            var components = URLComponents(string: "https://www.youtube.com/watch")!
            components.queryItems = [URLQueryItem(name: "v", value: videoID)]
            try await requireApproval(
                ToolApproval(
                    title: "打开 YouTube？",
                    message: title,
                    destructive: false
                ),
                approve
            )
            try await openExternalURL(components.url)
            return ["opened": .bool(true), "video_id": .string(videoID)]

        case "open_google_maps_search":
            let query = try arguments.requiredString("query")
            let url = try mapsURL(
                path: "/maps/search/",
                items: [URLQueryItem(name: "query", value: query)]
            )
            try await requireApproval(
                ToolApproval(
                    title: "在 Google Maps 中搜索？",
                    message: query,
                    destructive: false
                ),
                approve
            )
            try await openExternalURL(url)
            return ["opened": .bool(true), "query": .string(query)]

        case "open_google_maps_directions":
            let destination = try arguments.requiredString("destination")
            let mode = try arguments.requiredString("travel_mode")
            guard ["driving", "walking", "bicycling", "transit"].contains(mode) else {
                throw WellphoneError.invalidArguments("不支持的 Google Maps 出行方式")
            }
            var items = [
                URLQueryItem(name: "destination", value: destination),
                URLQueryItem(name: "travelmode", value: mode),
            ]
            if let origin = arguments["origin"]?.string, !origin.isEmpty {
                items.append(URLQueryItem(name: "origin", value: origin))
            }
            let url = try mapsURL(path: "/maps/dir/", items: items)
            try await requireApproval(
                ToolApproval(
                    title: "在 Google Maps 中规划路线？",
                    message: "目的地：\(destination)",
                    destructive: false
                ),
                approve
            )
            try await openExternalURL(url)
            return ["opened": .bool(true), "destination": .string(destination)]

        default:
            throw WellphoneError.invalidArguments("未知工具 \(name)")
        }
    }

    private func selectAlbum(identifier: String, name: String) throws {
        if let albumName, albumName != name {
            throw WellphoneError.toolScopeViolation("一次运行只能操作一个目标相册：\(albumName)")
        }
        albumName = name
        writableAlbumIDs.insert(identifier)
    }

    private func requireAllowed(_ identifiers: [String]) throws {
        let unknown = Set(identifiers).subtracting(allowedPhotoIDs)
        guard unknown.isEmpty else {
            throw WellphoneError.toolScopeViolation(
                "照片不属于本次 search_photos 结果：\(unknown.sorted().joined(separator: ", "))"
            )
        }
    }

    private func requireWritableAlbum(_ identifier: String) throws {
        guard writableAlbumIDs.contains(identifier) else {
            throw WellphoneError.toolScopeViolation(
                "相册必须先由本次 find_album 或 create_album 返回"
            )
        }
    }

    private func requireApproval(
        _ request: ToolApproval,
        _ approve: (ToolApproval) async -> Bool
    ) async throws {
        guard await approve(request) else {
            throw WellphoneError.userDeclined(request.title)
        }
    }

    private func recipients(_ values: [String]) throws -> [String] {
        guard values.allSatisfy({
            $0.contains("@") && !$0.contains("\n") && !$0.contains("\r")
        }) else {
            throw WellphoneError.invalidArguments("邮件地址格式无效")
        }
        return values
    }

    private func strings(_ values: [String]) -> JSONValue {
        .array(values.map(JSONValue.string))
    }

    private func parseDate(_ value: String) throws -> Date {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) {
            return date
        }
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: value) else {
            throw WellphoneError.invalidArguments("日期必须是带时区的 ISO 8601")
        }
        return date
    }

    private func mapsURL(path: String, items: [URLQueryItem]) throws -> URL {
        var components = URLComponents()
        components.scheme = "https"
        components.host = "www.google.com"
        components.path = path
        components.queryItems = [URLQueryItem(name: "api", value: "1")] + items
        guard let url = components.url else {
            throw WellphoneError.invalidArguments("无法生成 Google Maps 链接")
        }
        return url
    }

    private func openExternalURL(_ url: URL?) async throws {
        guard let url, await UIApplication.shared.open(url) else {
            throw WellphoneError.externalAppUnavailable
        }
    }
}
