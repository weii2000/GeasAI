@preconcurrency import Photos
import Foundation
import ImageIO
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

    func search(start: Date, end: Date) throws -> [PhotoSummary] {
        guard start < end else {
            throw WellphoneError.invalidArguments("start 必须早于 end")
        }
        let options = PHFetchOptions()
        options.predicate = NSPredicate(
            format: "creationDate >= %@ AND creationDate < %@",
            start as NSDate,
            end as NSDate
        )
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: true)]

        let assets = PHAsset.fetchAssets(with: .image, options: options)
        var photos: [PhotoSummary] = []
        let formatter = ISO8601DateFormatter()
        assets.enumerateObjects { asset, _, _ in
            guard !asset.mediaSubtypes.contains(.photoScreenshot) else { return }
            photos.append(
                PhotoSummary(
                    identifier: asset.localIdentifier,
                    createdAt: asset.creationDate.map {
                        formatter.string(from: $0)
                    },
                    width: asset.pixelWidth,
                    height: asset.pixelHeight,
                    isFavorite: asset.isFavorite
                )
            )
        }
        return photos
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
        options.isNetworkAccessAllowed = true

        let manager = PHImageManager.default()
        let cancellation = ImageRequestCancellation(manager: manager)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let requestID = manager.requestImageDataAndOrientation(
                    for: asset,
                    options: options
                ) { data, _, orientation, info in
                    if let error = info?[PHImageErrorKey] as? Error {
                        continuation.resume(throwing: error)
                    } else if (info?[PHImageCancelledKey] as? Bool) == true {
                        continuation.resume(throwing: CancellationError())
                    } else if let data {
                        continuation.resume(
                            returning: PhotoImage(data: data, orientation: orientation)
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
}

struct OCRService: Sendable {
    func recognize(_ image: PhotoImage) async throws -> String {
        var request = RecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.automaticallyDetectsLanguage = true
        request.usesLanguageCorrection = true
        let observations = try await request.perform(
            on: image.data,
            orientation: image.orientation
        )
        return observations.map(\.transcript).joined(separator: "\n")
    }
}
