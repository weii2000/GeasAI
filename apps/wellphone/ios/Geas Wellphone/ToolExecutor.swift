import Foundation

@MainActor
final class ToolExecutor {
    private let photos = PhotoService()
    private let ocr = OCRService()
    private var allowedPhotoIDs: Set<String> = []
    private var ownedAlbumIDs: Set<String> = []
    private var initialSearchRange: (start: Date, end: Date)?
    private var albumName: String?
    private var scopeLocked = false

    func resetScope() {
        allowedPhotoIDs.removeAll()
        ownedAlbumIDs.removeAll()
        initialSearchRange = nil
        albumName = nil
        scopeLocked = false
    }

    func requirePermission() async throws {
        try await photos.requireFullAccess()
    }

    func execute(
        _ call: ToolCall,
        onProgress: (String) -> Void = { _ in }
    ) async -> ToolResultRequest {
        do {
            let result = try await execute(
                name: call.name,
                arguments: call.arguments,
                onProgress: onProgress
            )
            return ToolResultRequest(
                callID: call.callID,
                result: result,
                isError: false
            )
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
        onProgress: (String) -> Void
    ) async throws -> [String: JSONValue] {
        switch name {
        case "search_photos":
            let start = try parseDate(arguments.requiredString("start"))
            let end = try parseDate(arguments.requiredString("end"))
            guard !scopeLocked else {
                throw WellphoneError.toolScopeViolation(
                    "开始分析照片后不能扩大搜索范围"
                )
            }
            if let initialSearchRange {
                guard start >= initialSearchRange.start,
                      end <= initialSearchRange.end else {
                    throw WellphoneError.toolScopeViolation(
                        "后续搜索必须位于首次搜索区间内"
                    )
                }
            }
            let results = try photos.search(start: start, end: end)
            if initialSearchRange == nil {
                initialSearchRange = (start, end)
            }
            allowedPhotoIDs.formUnion(results.map(\.identifier))
            return [
                "count": .number(Double(results.count)),
                "photos": .array(results.map(\.json)),
            ]

        case "analyze_photos":
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireAllowed(identifiers)
            guard albumName != nil else {
                throw WellphoneError.toolScopeViolation(
                    "必须先用 create_album 确定本次唯一目标相册"
                )
            }
            scopeLocked = true
            guard identifiers.count <= 12 else {
                throw WellphoneError.invalidArguments("每批最多分析 12 张照片")
            }
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
                } catch {
                    results.append(.object([
                        "identifier": .string(identifier),
                        "error": .string(error.localizedDescription),
                    ]))
                }
            }
            return ["analyses": .array(results)]

        case "create_album":
            let name = try arguments.requiredString("name")
            if let albumName, albumName != name {
                throw WellphoneError.toolScopeViolation(
                    "一次任务只能操作一个目标相册：\(albumName)"
                )
            }
            let identifier = try await photos.findOrCreateAlbum(named: name)
            albumName = name
            ownedAlbumIDs.insert(identifier)
            return [
                "album_id": .string(identifier),
                "name": .string(name),
            ]

        case "add_photos_to_album":
            let albumID = try arguments.requiredString("album_id")
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireOwnedAlbum(albumID)
            try requireAllowed(identifiers)
            let result = try await photos.addPhotos(
                identifiers: identifiers,
                toAlbum: albumID
            )
            return [
                "added_count": .number(Double(result.added)),
                "missing_identifiers": .array(result.missing.map(JSONValue.string)),
            ]

        case "get_album_contents":
            let albumID = try arguments.requiredString("album_id")
            try requireOwnedAlbum(albumID)
            let identifiers = try photos.albumContents(identifier: albumID)
            return [
                "count": .number(Double(identifiers.count)),
                "identifiers": .array(identifiers.map(JSONValue.string)),
            ]

        default:
            throw WellphoneError.invalidArguments("未知工具 \(name)")
        }
    }

    private func requireAllowed(_ identifiers: [String]) throws {
        let unknown = Set(identifiers).subtracting(allowedPhotoIDs)
        guard unknown.isEmpty else {
            throw WellphoneError.toolScopeViolation(
                "照片不属于本次 search_photos 结果：\(unknown.sorted().joined(separator: ", "))"
            )
        }
    }

    private func requireOwnedAlbum(_ identifier: String) throws {
        guard ownedAlbumIDs.contains(identifier) else {
            throw WellphoneError.toolScopeViolation(
                "相册必须先由本次 create_album 返回"
            )
        }
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
}
