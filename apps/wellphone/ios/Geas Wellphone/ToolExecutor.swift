import Foundation
import MessageUI

@MainActor
final class ToolExecutor {
    private let photos = PhotoService()
    private let ocr = OCRService()
    private let location = LocationService()
    private let health = HealthService()
    private let workouts = WorkoutService()
    private let contacts = ContactService()
    private let reminders = ReminderService()
    private var allowedPhotoIDs: Set<String> = []
    private var writableAlbumIDs: Set<String> = []
    private var initialSearchRange: (start: Date, end: Date)?
    private var albumName: String?
    private var scopeLocked = false
    private var allowedWorkoutIDs: Set<String> = []

    func resetScope() {
        allowedPhotoIDs.removeAll()
        writableAlbumIDs.removeAll()
        initialSearchRange = nil
        albumName = nil
        scopeLocked = false
        allowedWorkoutIDs.removeAll()
    }

    func prepareMail(_ draft: MailDraft) async throws -> MailPresentation {
        let attachments = try await photos.mailAttachments(
            identifiers: draft.attachmentPhotoIDs
        )
        return MailPresentation(draft: draft, attachments: attachments)
    }

    func execute(
        _ call: ToolCall,
        onProgress: (String) -> Void = { _ in },
        approve: (ToolApproval) async -> Bool = { _ in false },
        onPendingAction: (PendingAction) -> Void = { _ in }
    ) async -> ToolResultRequest {
        do {
            guard let name = ToolName(rawValue: call.name) else {
                throw WellphoneError.invalidArguments("未知工具 \(call.name)")
            }
            if name.requiresPhotoAccess {
                try await photos.requireFullAccess()
            }
            let result = try await execute(
                name: name,
                taskID: call.taskID,
                actionID: call.callID,
                arguments: call.arguments,
                onProgress: onProgress,
                approve: approve,
                onPendingAction: onPendingAction
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
        name: ToolName,
        taskID: String,
        actionID: String,
        arguments: [String: JSONValue],
        onProgress: (String) -> Void,
        approve: (ToolApproval) async -> Bool,
        onPendingAction: (PendingAction) -> Void
    ) async throws -> [String: JSONValue] {
        switch name {
        case .confirmMCPAction:
            let server = try arguments.requiredString("server")
            let tool = try arguments.requiredString("tool")
            let preview = try arguments.requiredString("arguments_preview")
            guard server.count <= 64, tool.count <= 128, preview.count <= 800 else {
                throw WellphoneError.invalidArguments("MCP 确认信息过长")
            }
            try await requireApproval(
                ToolApproval(
                    title: "允许修改外部服务？",
                    message: "\(server) 将执行 \(tool)：\n\(preview)",
                    destructive: false
                ),
                approve
            )
            return ["approved": .bool(true)]

        case .getCurrentLocation:
            return try await location.currentLocation()

        case .geocodeAddress:
            let results = try await location.geocode(
                address: arguments.requiredString("address"),
                limit: arguments.requiredInteger("max_results")
            )
            return ["count": .number(Double(results.count)), "results": .array(results)]

        case .reverseGeocodeLocation:
            let results = try await location.reverseGeocode(
                latitude: arguments.requiredNumber("latitude"),
                longitude: arguments.requiredNumber("longitude")
            )
            return ["count": .number(Double(results.count)), "results": .array(results)]

        case .searchNearbyPlaces:
            let results = try await location.nearby(
                query: arguments.requiredString("query"),
                radius: arguments.requiredNumber("radius_meters"),
                limit: arguments.requiredInteger("max_results")
            )
            return ["count": .number(Double(results.count)), "places": .array(results)]

        case .getHealthSummary:
            let range = try validatedRange(arguments, maximumDays: 31)
            return try await health.activitySummary(start: range.start, end: range.end)

        case .getSleepSummary:
            let range = try validatedRange(arguments, maximumDays: 31)
            return try await health.sleepSummary(start: range.start, end: range.end)

        case .listHealthWorkouts:
            let range = try validatedRange(arguments, maximumDays: 90)
            let limit = try arguments.requiredInteger("limit")
            guard (1...50).contains(limit) else {
                throw WellphoneError.invalidArguments("limit 必须在 1 到 50 之间")
            }
            let results = try await health.workouts(
                start: range.start,
                end: range.end,
                limit: limit
            )
            return ["count": .number(Double(results.count)), "workouts": .array(results)]

        case .listScheduledWorkouts:
            let results = try await workouts.scheduledWorkouts()
            allowedWorkoutIDs.formUnion(results.compactMap {
                $0.object?["workout_id"]?.string
            })
            return ["count": .number(Double(results.count)), "workouts": .array(results)]

        case .scheduleWorkout:
            let activity = try arguments.requiredString("activity")
            let workoutLocation = try arguments.requiredString("location")
            let goalType = try arguments.requiredString("goal_type")
            let goalValue = try arguments.optionalNumber("goal_value")
            guard ["walking", "running", "cycling", "swimming"].contains(activity),
                  ["indoor", "outdoor", "unknown"].contains(workoutLocation),
                  ["open", "time_minutes", "distance_km", "energy_kcal"].contains(goalType),
                  goalType == "open" || (goalValue ?? 0) > 0 else {
                throw WellphoneError.invalidArguments("训练类型、地点或目标无效")
            }
            let scheduledAt = try parseDate(arguments.requiredString("scheduled_at"))
            guard scheduledAt > .now,
                  scheduledAt <= Calendar.current.date(
                    byAdding: .year,
                    value: 1,
                    to: .now
                  )! else {
                throw WellphoneError.invalidArguments("训练时间必须在未来一年内")
            }
            try await requireApproval(
                ToolApproval(
                    title: "安排训练？",
                    message: "将训练计划添加到 Apple Watch。",
                    destructive: false
                ),
                approve
            )
            let result = try await workouts.schedule(
                actionID: actionID,
                activityName: activity,
                locationName: workoutLocation,
                goalType: goalType,
                goalValue: goalValue,
                date: scheduledAt
            )
            allowedWorkoutIDs.insert(result.id)
            return [
                "workout_id": .string(result.id),
                "scheduled": .bool(true),
                "already_scheduled": .bool(result.alreadyScheduled),
            ]

        case .removeScheduledWorkout:
            let workoutID = try arguments.requiredString("workout_id").lowercased()
            guard allowedWorkoutIDs.contains(workoutID), let id = UUID(uuidString: workoutID) else {
                throw WellphoneError.toolScopeViolation(
                    "训练必须先由本次 list_scheduled_workouts 返回"
                )
            }
            try await requireApproval(
                ToolApproval(
                    title: "移除训练计划？",
                    message: "将从 Apple Watch 训练计划中移除此项。",
                    destructive: true
                ),
                approve
            )
            let removed = try await workouts.remove(id: id)
            return [
                "removed": .bool(removed),
                "already_absent": .bool(!removed),
            ]

        case .searchContacts:
            let results = try contacts.search(
                query: arguments.requiredString("query"),
                limit: arguments.requiredInteger("limit")
            )
            return ["count": .number(Double(results.count)), "contacts": .array(results)]

        case .createReminder:
            let title = try arguments.requiredString("title")
            let notes: String?
            if let value = arguments["notes"] {
                guard let string = value.string else {
                    throw WellphoneError.invalidArguments("notes 必须是字符串")
                }
                notes = string
            } else {
                notes = nil
            }
            let priority: String
            if let value = arguments["priority"] {
                guard let string = value.string else {
                    throw WellphoneError.invalidArguments("priority 必须是字符串")
                }
                priority = string
            } else {
                priority = "none"
            }
            guard title.count <= 200,
                  (notes?.count ?? 0) <= 2_000,
                  ["none", "low", "medium", "high"].contains(priority) else {
                throw WellphoneError.invalidArguments("提醒标题、备注或优先级无效")
            }
            let dueAt: Date?
            if let value = arguments["due_at"] {
                guard let string = value.string else {
                    throw WellphoneError.invalidArguments("due_at 必须是字符串")
                }
                dueAt = try parseDate(string)
            } else {
                dueAt = nil
            }
            if let dueAt, dueAt <= .now {
                throw WellphoneError.invalidArguments("提醒时间必须晚于当前时间")
            }
            try await requireApproval(
                ToolApproval(
                    title: "创建提醒？",
                    message: dueAt.map {
                        "将创建“\(title)”，到期时间为 \($0.formatted())。"
                    } ?? "将创建“\(title)”，不设置到期时间。",
                    destructive: false
                ),
                approve
            )
            return try await reminders.create(
                actionID: "\(taskID):\(actionID)",
                title: title,
                notes: notes,
                dueAt: dueAt,
                priorityName: priority
            )

        case .searchPhotos:
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

        case .getPhotoDetails:
            let identifiers = try arguments.requiredStrings("identifiers")
            try requireAllowed(identifiers)
            let result = photos.details(identifiers: identifiers)
            return [
                "photos": .array(result.photos.map(\.json)),
                "missing_identifiers": strings(result.missing),
            ]

        case .analyzePhotos:
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

        case .listAlbums:
            let albums = photos.listAlbums()
            return [
                "count": .number(Double(albums.count)),
                "albums": .array(albums.map(\.json)),
            ]

        case .findAlbum:
            let name = try arguments.requiredString("name")
            guard !scopeLocked || albumName != nil else {
                throw WellphoneError.toolScopeViolation("照片分析后不能再选择目标相册")
            }
            let identifier = try photos.findAlbumID(named: name)
            try selectAlbum(identifier: identifier, name: name)
            return ["album_id": .string(identifier), "name": .string(name)]

        case .createAlbum:
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

        case .renameAlbum:
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

        case .deleteAlbum:
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

        case .addPhotosToAlbum:
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

        case .removePhotosFromAlbum:
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

        case .getAlbumContents:
            let albumID = try arguments.requiredString("album_id")
            try requireWritableAlbum(albumID)
            let identifiers = try photos.albumContents(identifier: albumID)
            return [
                "count": .number(Double(identifiers.count)),
                "identifiers": strings(identifiers),
            ]

        case .setFavorite:
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

        case .setHidden:
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

        case .setPhotoCreationDate:
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

        case .setPhotoLocation:
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

        case .deletePhotos:
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

        case .composeEmail:
            guard MFMailComposeViewController.canSendMail() else {
                throw WellphoneError.mailUnavailable
            }
            let attachmentIDs = try arguments.optionalStrings("attachment_photo_ids")
            guard attachmentIDs.count <= 3 else {
                throw WellphoneError.attachmentLimit
            }
            if !attachmentIDs.isEmpty {
                try requireAllowed(attachmentIDs)
                try await photos.requireFullAccess()
            }
            let subject = try arguments.requiredString("subject")
            let body = try arguments.requiredString("body")
            guard subject.count <= 500, body.count <= 100_000 else {
                throw WellphoneError.invalidArguments("邮件主题或正文过长")
            }
            let draft = MailDraft(
                id: actionID,
                to: try recipients(arguments.requiredStrings("to")),
                cc: try recipients(arguments.optionalStrings("cc")),
                bcc: try recipients(arguments.optionalStrings("bcc")),
                subject: subject,
                body: body,
                isHTML: try arguments.optionalBool("is_html") ?? false,
                attachmentPhotoIDs: attachmentIDs
            )
            onPendingAction(
                PendingAction(
                    id: actionID,
                    kind: .mail,
                    title: "邮件草稿已准备",
                    detail: draft.subject,
                    buttonTitle: "检查邮件",
                    url: nil,
                    mailDraft: draft
                )
            )
            return [
                "prepared": .bool(true),
                "requires_user_send": .bool(true),
                "recipient_count": .number(Double(draft.to.count + draft.cc.count + draft.bcc.count)),
                "attachment_count": .number(Double(attachmentIDs.count)),
            ]

        case .openYouTubeVideo:
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
            guard let url = components.url else {
                throw WellphoneError.invalidArguments("无法生成 YouTube 链接")
            }
            onPendingAction(
                PendingAction(
                    id: actionID,
                    kind: .url,
                    title: "YouTube 视频已准备",
                    detail: title,
                    buttonTitle: "在 YouTube 中打开",
                    url: url,
                    mailDraft: nil
                )
            )
            return [
                "prepared": .bool(true),
                "requires_user_open": .bool(true),
                "video_id": .string(videoID),
            ]

        case .openGoogleMapsSearch:
            let query = try arguments.requiredString("query")
            let url = try mapsURL(
                path: "/maps/search/",
                items: [URLQueryItem(name: "query", value: query)]
            )
            onPendingAction(
                PendingAction(
                    id: actionID,
                    kind: .url,
                    title: "地图搜索已准备",
                    detail: query,
                    buttonTitle: "在 Google Maps 中打开",
                    url: url,
                    mailDraft: nil
                )
            )
            return [
                "prepared": .bool(true),
                "requires_user_open": .bool(true),
                "query": .string(query),
            ]

        case .openGoogleMapsDirections:
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
            onPendingAction(
                PendingAction(
                    id: actionID,
                    kind: .url,
                    title: "路线已准备",
                    detail: "目的地：\(destination)",
                    buttonTitle: "在 Google Maps 中打开",
                    url: url,
                    mailDraft: nil
                )
            )
            return [
                "prepared": .bool(true),
                "requires_user_open": .bool(true),
                "destination": .string(destination),
            ]

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
        guard values.count <= 50, values.allSatisfy({
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

    private func validatedRange(
        _ arguments: [String: JSONValue],
        maximumDays: Double
    ) throws -> (start: Date, end: Date) {
        let start = try parseDate(arguments.requiredString("start"))
        let end = try parseDate(arguments.requiredString("end"))
        guard start < end else {
            throw WellphoneError.invalidArguments("start 必须早于 end")
        }
        guard end.timeIntervalSince(start) <= maximumDays * 86_400 else {
            throw WellphoneError.invalidArguments("日期范围不能超过 \(Int(maximumDays)) 天")
        }
        return (start, end)
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

}
