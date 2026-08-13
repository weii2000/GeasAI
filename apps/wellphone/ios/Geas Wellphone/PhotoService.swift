@preconcurrency import Photos
import CoreLocation
import Foundation
import ImageIO
import UIKit
import Vision

private final class ImageRequestCancellation: @unchecked Sendable {
    private let manager: PHImageManager
    private let lock = NSLock()
    private var requestID = PHInvalidImageRequestID
    private var isCancelled = false

    init(manager: PHImageManager) {
        self.manager = manager
    }

    func register(_ id: PHImageRequestID) {
        lock.lock()
        requestID = id
        let shouldCancel = isCancelled
        lock.unlock()
        if shouldCancel {
            manager.cancelImageRequest(id)
        }
    }

    func cancel() {
        lock.lock()
        isCancelled = true
        let id = requestID
        lock.unlock()
        if id != PHInvalidImageRequestID {
            manager.cancelImageRequest(id)
        }
    }
}

@MainActor
final class PhotoService {
    func requireFullAccess() async throws {
        let status = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        guard status == .authorized else {
            throw WellphoneError.fullPhotoAccessRequired
        }
    }

    func search(
        start: Date,
        end: Date,
        mediaType: String,
        includeScreenshots: Bool,
        favorite: Bool?,
        hidden: Bool?
    ) throws -> [PhotoSummary] {
        guard start < end else {
            throw WellphoneError.invalidArguments("start 必须早于 end")
        }
        let options = PHFetchOptions()
        options.fetchLimit = 200
        var predicates = [
            NSPredicate(
                format: "creationDate >= %@ AND creationDate < %@",
                start as NSDate,
                end as NSDate
            )
        ]
        if let favorite {
            predicates.append(NSPredicate(format: "favorite == %@", favorite as NSNumber))
        }
        if let hidden {
            predicates.append(NSPredicate(format: "hidden == %@", hidden as NSNumber))
            options.includeHiddenAssets = hidden
        }
        options.predicate = NSCompoundPredicate(andPredicateWithSubpredicates: predicates)
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]

        let assets: PHFetchResult<PHAsset>
        switch mediaType {
        case "image":
            assets = PHAsset.fetchAssets(with: .image, options: options)
        case "video":
            assets = PHAsset.fetchAssets(with: .video, options: options)
        case "any":
            assets = PHAsset.fetchAssets(with: options)
        default:
            throw WellphoneError.invalidArguments("media_type 必须是 image、video 或 any")
        }
        var photos: [PhotoSummary] = []
        assets.enumerateObjects { asset, _, _ in
            guard includeScreenshots
                    || !asset.mediaSubtypes.contains(.photoScreenshot) else { return }
            photos.append(self.summary(asset))
        }
        return photos
    }

    func details(
        identifiers: [String]
    ) -> (photos: [PhotoSummary], missing: [String]) {
        let result = fetchAssets(identifiers)
        return (result.assets.map(summary), result.missing)
    }

    func image(identifier: String) async throws -> PhotoImage {
        guard let asset = PHAsset.fetchAssets(
            withLocalIdentifiers: [identifier],
            options: nil
        ).firstObject else {
            throw WellphoneError.missingPhoto(identifier)
        }

        let options = PHImageRequestOptions()
        options.version = .current
        options.deliveryMode = .highQualityFormat
        options.resizeMode = .fast
        options.isNetworkAccessAllowed = true

        let manager = PHImageManager.default()
        let cancellation = ImageRequestCancellation(manager: manager)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let requestID = manager.requestImage(
                    for: asset,
                    targetSize: CGSize(width: 2_048, height: 2_048),
                    contentMode: .aspectFit,
                    options: options
                ) { image, info in
                    if (info?[PHImageResultIsDegradedKey] as? Bool) == true {
                        return
                    }
                    if let error = info?[PHImageErrorKey] as? Error {
                        continuation.resume(throwing: error)
                    } else if (info?[PHImageCancelledKey] as? Bool) == true {
                        continuation.resume(throwing: CancellationError())
                    } else if let image, let cgImage = image.cgImage {
                        continuation.resume(
                            returning: PhotoImage(
                                image: cgImage,
                                orientation: image.imageOrientation.cgOrientation
                            )
                        )
                    } else {
                        continuation.resume(
                            throwing: WellphoneError.missingPhoto(identifier)
                        )
                    }
                }
                cancellation.register(requestID)
            }
        } onCancel: {
            cancellation.cancel()
        }
    }

    func findOrCreateAlbum(named name: String) async throws -> String {
        if let album = findAlbum(named: name) {
            return album.localIdentifier
        }
        try await PHPhotoLibrary.shared().performChanges {
            PHAssetCollectionChangeRequest.creationRequestForAssetCollection(
                withTitle: name
            )
        }
        guard let album = findAlbum(named: name) else {
            throw WellphoneError.missingAlbum(name)
        }
        return album.localIdentifier
    }

    func findAlbumID(named name: String) throws -> String {
        guard let album = findAlbum(named: name) else {
            throw WellphoneError.missingAlbum(name)
        }
        return album.localIdentifier
    }

    func listAlbums() -> [AlbumSummary] {
        let albums = PHAssetCollection.fetchAssetCollections(
            with: .album,
            subtype: .any,
            options: nil
        )
        var summaries: [AlbumSummary] = []
        albums.enumerateObjects { album, _, _ in
            summaries.append(
                AlbumSummary(
                    identifier: album.localIdentifier,
                    name: album.localizedTitle ?? "未命名相册",
                    count: PHAsset.fetchAssets(in: album, options: nil).count
                )
            )
        }
        return summaries.sorted {
            $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }

    func renameAlbum(identifier: String, to name: String) async throws {
        guard let album = findAlbum(identifier: identifier) else {
            throw WellphoneError.missingAlbum(identifier)
        }
        guard album.canPerform(.rename) else {
            throw WellphoneError.albumNotWritable(identifier)
        }
        try await PHPhotoLibrary.shared().performChanges {
            guard let request = PHAssetCollectionChangeRequest(for: album) else { return }
            request.title = name
        }
        guard findAlbum(identifier: identifier)?.localizedTitle == name else {
            throw WellphoneError.invalidArguments("相册重命名未生效")
        }
    }

    func deleteAlbum(identifier: String) async throws {
        guard let album = findAlbum(identifier: identifier) else {
            throw WellphoneError.missingAlbum(identifier)
        }
        guard album.canPerform(.delete) else {
            throw WellphoneError.albumNotWritable(identifier)
        }
        try await PHPhotoLibrary.shared().performChanges {
            PHAssetCollectionChangeRequest.deleteAssetCollections([album] as NSArray)
        }
        guard findAlbum(identifier: identifier) == nil else {
            throw WellphoneError.invalidArguments("相册删除未生效")
        }
    }

    func addPhotos(
        identifiers: [String],
        toAlbum albumIdentifier: String
    ) async throws -> (added: Int, missing: [String]) {
        guard let album = findAlbum(identifier: albumIdentifier) else {
            throw WellphoneError.missingAlbum(albumIdentifier)
        }
        guard album.canPerform(.addContent) else {
            throw WellphoneError.albumNotWritable(albumIdentifier)
        }

        let existing = Set(try albumContents(identifier: albumIdentifier))
        let requested = Array(Set(identifiers)).filter { !existing.contains($0) }
        let fetched = PHAsset.fetchAssets(
            withLocalIdentifiers: requested,
            options: nil
        )
        var assets: [PHAsset] = []
        fetched.enumerateObjects { asset, _, _ in assets.append(asset) }
        let found = Set(assets.map(\.localIdentifier))

        if !assets.isEmpty {
            try await PHPhotoLibrary.shared().performChanges {
                guard let request = PHAssetCollectionChangeRequest(for: album) else {
                    return
                }
                request.addAssets(assets as NSArray)
            }
        }
        let updated = Set(try albumContents(identifier: albumIdentifier))
        let added = found.filter { !existing.contains($0) && updated.contains($0) }
        let notAdded = requested.filter { !updated.contains($0) }
        return (added.count, notAdded)
    }

    func albumContents(identifier: String) throws -> [String] {
        guard let album = findAlbum(identifier: identifier) else {
            throw WellphoneError.missingAlbum(identifier)
        }
        let assets = PHAsset.fetchAssets(in: album, options: nil)
        var identifiers: [String] = []
        assets.enumerateObjects { asset, _, _ in
            identifiers.append(asset.localIdentifier)
        }
        return identifiers
    }

    func removePhotos(
        identifiers: [String],
        fromAlbum albumIdentifier: String
    ) async throws -> (removed: Int, notPresent: [String]) {
        guard let album = findAlbum(identifier: albumIdentifier) else {
            throw WellphoneError.missingAlbum(albumIdentifier)
        }
        guard album.canPerform(.removeContent) else {
            throw WellphoneError.albumNotWritable(albumIdentifier)
        }
        let requested = Set(identifiers)
        let contents = PHAsset.fetchAssets(in: album, options: nil)
        var assets: [PHAsset] = []
        contents.enumerateObjects { asset, _, _ in
            if requested.contains(asset.localIdentifier) {
                assets.append(asset)
            }
        }
        let present = Set(assets.map(\.localIdentifier))
        if !assets.isEmpty {
            try await PHPhotoLibrary.shared().performChanges {
                guard let request = PHAssetCollectionChangeRequest(for: album) else {
                    return
                }
                request.removeAssets(assets as NSArray)
            }
        }
        let updated = Set(try albumContents(identifier: albumIdentifier))
        let removed = assets.filter { !updated.contains($0.localIdentifier) }.count
        return (removed, identifiers.filter { !present.contains($0) })
    }

    func setFavorite(
        identifiers: [String],
        value: Bool
    ) async throws -> (updated: Int, missing: [String]) {
        let result = fetchAssets(identifiers)
        try requirePropertyEditing(result.assets)
        try await PHPhotoLibrary.shared().performChanges {
            for asset in result.assets {
                PHAssetChangeRequest(for: asset).isFavorite = value
            }
        }
        let current = details(identifiers: identifiers)
        return (current.photos.filter { $0.isFavorite == value }.count, current.missing)
    }

    func setHidden(
        identifiers: [String],
        value: Bool
    ) async throws -> (updated: Int, missing: [String]) {
        let result = fetchAssets(identifiers)
        try requirePropertyEditing(result.assets)
        try await PHPhotoLibrary.shared().performChanges {
            for asset in result.assets {
                PHAssetChangeRequest(for: asset).isHidden = value
            }
        }
        let current = details(identifiers: identifiers)
        return (current.photos.filter { $0.isHidden == value }.count, current.missing)
    }

    func setCreationDate(identifier: String, date: Date) async throws {
        let result = fetchAssets([identifier])
        guard let asset = result.assets.first else {
            throw WellphoneError.missingPhoto(identifier)
        }
        try requirePropertyEditing([asset])
        try await PHPhotoLibrary.shared().performChanges {
            PHAssetChangeRequest(for: asset).creationDate = date
        }
    }

    func setLocation(
        identifiers: [String],
        latitude: Double,
        longitude: Double
    ) async throws -> (updated: Int, missing: [String]) {
        let result = fetchAssets(identifiers)
        try requirePropertyEditing(result.assets)
        let location = CLLocation(latitude: latitude, longitude: longitude)
        try await PHPhotoLibrary.shared().performChanges {
            for asset in result.assets {
                PHAssetChangeRequest(for: asset).location = location
            }
        }
        return (result.assets.count, result.missing)
    }

    func deletePhotos(
        identifiers: [String]
    ) async throws -> (deleted: Int, missing: [String]) {
        let result = fetchAssets(identifiers)
        guard result.assets.allSatisfy({ $0.canPerform(.delete) }) else {
            throw WellphoneError.invalidArguments("部分照片不允许删除")
        }
        if !result.assets.isEmpty {
            try await PHPhotoLibrary.shared().performChanges {
                PHAssetChangeRequest.deleteAssets(result.assets as NSArray)
            }
        }
        let remaining = fetchAssets(identifiers).assets.count
        return (result.assets.count - remaining, result.missing)
    }

    private func findAlbum(named name: String) -> PHAssetCollection? {
        let options = PHFetchOptions()
        options.predicate = NSPredicate(format: "localizedTitle == %@", name)
        return PHAssetCollection.fetchAssetCollections(
            with: .album,
            subtype: .any,
            options: options
        ).firstObject
    }

    private func findAlbum(identifier: String) -> PHAssetCollection? {
        PHAssetCollection.fetchAssetCollections(
            withLocalIdentifiers: [identifier],
            options: nil
        ).firstObject
    }

    private func fetchAssets(
        _ identifiers: [String]
    ) -> (assets: [PHAsset], missing: [String]) {
        let unique = Array(Set(identifiers))
        let fetched = PHAsset.fetchAssets(withLocalIdentifiers: unique, options: nil)
        var byID: [String: PHAsset] = [:]
        fetched.enumerateObjects { asset, _, _ in
            byID[asset.localIdentifier] = asset
        }
        return (
            unique.compactMap { byID[$0] },
            unique.filter { byID[$0] == nil }
        )
    }

    private func requirePropertyEditing(_ assets: [PHAsset]) throws {
        guard assets.allSatisfy({ $0.canPerform(.properties) }) else {
            throw WellphoneError.invalidArguments("部分照片不允许修改属性")
        }
    }

    private func summary(_ asset: PHAsset) -> PhotoSummary {
        let formatter = ISO8601DateFormatter()
        let mediaType = switch asset.mediaType {
        case .image: "image"
        case .video: "video"
        case .audio: "audio"
        default: "unknown"
        }
        return PhotoSummary(
            identifier: asset.localIdentifier,
            createdAt: asset.creationDate.map { formatter.string(from: $0) },
            width: asset.pixelWidth,
            height: asset.pixelHeight,
            isFavorite: asset.isFavorite,
            isHidden: asset.isHidden,
            isScreenshot: asset.mediaSubtypes.contains(.photoScreenshot),
            mediaType: mediaType,
            duration: asset.duration,
            addedAt: formatter.string(from: asset.addedDate),
            latitude: asset.location?.coordinate.latitude,
            longitude: asset.location?.coordinate.longitude
        )
    }
}

struct OCRService: Sendable {
    func recognize(_ image: PhotoImage) async throws -> String {
        var request = RecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.automaticallyDetectsLanguage = true
        request.usesLanguageCorrection = true
        let observations = try await request.perform(
            on: image.image,
            orientation: image.orientation
        )
        return observations.map(\.transcript).joined(separator: "\n")
    }
}

private extension UIImage.Orientation {
    var cgOrientation: CGImagePropertyOrientation {
        switch self {
        case .up: .up
        case .upMirrored: .upMirrored
        case .down: .down
        case .downMirrored: .downMirrored
        case .left: .left
        case .leftMirrored: .leftMirrored
        case .right: .right
        case .rightMirrored: .rightMirrored
        @unknown default: .up
        }
    }
}
