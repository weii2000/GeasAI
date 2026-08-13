import Foundation
import Security

actor APIClient {
    private struct CreateTaskRequest: Encodable {
        let id: String
        let sessionID: String?
        let prompt: String
        let deviceContext: String

        enum CodingKeys: String, CodingKey {
            case id
            case sessionID = "session_id"
            case prompt
            case deviceContext = "device_context"
        }
    }
    private struct ErrorResponse: Decodable { let error: String }
    private struct Acknowledgement: Codable, Sendable {
        let accepted: Bool?
        let cancelled: Bool?
    }

    private let baseURL: URL
    private let deviceID: String
    private let session: URLSession

    init(baseURL: URL, deviceID: String) throws {
        guard ["http", "https"].contains(baseURL.scheme?.lowercased()),
              baseURL.host != nil else {
            throw WellphoneError.invalidServerURL
        }
        self.baseURL = baseURL
        self.deviceID = deviceID
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: configuration)
    }

    func createTask(
        id: String,
        sessionID: String?,
        prompt: String,
        deviceContext: String
    ) async throws -> ServerTask {
        let body = try JSONEncoder().encode(
            CreateTaskRequest(
                id: id,
                sessionID: sessionID,
                prompt: prompt,
                deviceContext: deviceContext
            )
        )
        return try await retryingNetwork {
            try await request(
                path: ["tasks"],
                method: "POST",
                body: body
            )
        }
    }

    func serverSession(id: String) async throws -> ServerSession {
        try await retryingNetwork {
            try await request(path: ["sessions", id])
        }
    }

    func task(id: String) async throws -> ServerTask {
        try await retryingNetwork {
            try await request(path: ["tasks", id])
        }
    }

    func nextTool(taskID: String) async throws -> ToolPoll {
        try await retryingNetwork {
            try await request(
                path: ["tasks", taskID, "next-tool"],
                timeout: 30
            )
        }
    }

    func submit(taskID: String, result: ToolResultRequest) async throws {
        let body = try JSONEncoder().encode(result)
        let _: Acknowledgement = try await retryingNetwork {
            try await request(
                path: ["tasks", taskID, "tool-result"],
                method: "POST",
                body: body
            )
        }
    }

    func cancel(taskID: String) async throws {
        let _: Acknowledgement = try await retryingNetwork {
            try await request(
                path: ["tasks", taskID],
                method: "DELETE"
            )
        }
    }

    private func retryingNetwork<Response: Sendable>(
        _ operation: () async throws -> Response
    ) async throws -> Response {
        var lastError: URLError?
        for attempt in 0..<3 {
            do {
                return try await operation()
            } catch is CancellationError {
                throw CancellationError()
            } catch let error as URLError {
                lastError = error
                guard attempt < 2 else { break }
                try await Task.sleep(for: .milliseconds(250 * (attempt + 1)))
            }
        }
        throw lastError ?? URLError(.unknown)
    }

    private func request<Response: Decodable & Sendable>(
        path: [String],
        method: String = "GET",
        body: Data? = nil,
        timeout: TimeInterval = 15
    ) async throws -> Response {
        var url = baseURL
        for component in path {
            url.appendPathComponent(component)
        }
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.httpBody = body
        request.setValue(deviceID, forHTTPHeaderField: "X-Wellphone-Device-ID")
        if body != nil {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw WellphoneError.server("响应不是 HTTP")
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = (try? JSONDecoder().decode(ErrorResponse.self, from: data).error)
                ?? "HTTP \(http.statusCode)"
            throw WellphoneError.server(message)
        }
        return try JSONDecoder().decode(Response.self, from: data)
    }
}

enum DeviceIdentity {
    private static let service = "com.geas.wellphone"
    private static let account = "device-id"

    static func loadOrCreate() -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        if SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
           let data = item as? Data,
           let value = String(data: data, encoding: .utf8) {
            return value
        }

        let value = UUID().uuidString.lowercased()
        let data = Data(value.utf8)
        SecItemAdd([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
        ] as CFDictionary, nil)
        return value
    }
}
