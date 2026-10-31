import Combine
import CryptoKit
import Foundation
import Security
import SwiftUI

private struct PCConnection: Codable {
    let url: String
    let pin: String
    let token: String

    static func parse(_ code: String) throws -> PCConnection {
        let code = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard code.count < 2048 else { throw PCError.invalidConnection }
        let standard = code.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        guard let data = Data(base64Encoded: standard),
              let connection = try? JSONDecoder().decode(PCConnection.self, from: data),
              let url = URLComponents(string: connection.url), url.scheme == "https",
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty, let host = url.host, let port = url.port, (1024...65535).contains(port),
              connection.pin.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              connection.token.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil
        else { throw PCError.invalidConnection }
        let octets = host.split(separator: ".").compactMap { Int($0) }
        guard octets.count == 4, octets.allSatisfy({ (0...255).contains($0) }),
              octets[0] == 10 || (octets[0] == 192 && octets[1] == 168)
                || (octets[0] == 172 && (16...31).contains(octets[1]))
        else { throw PCError.invalidConnection }
        return connection
    }
}

private enum PCError: LocalizedError {
    case invalidConnection, rejected, invalidCapture, failed(String)
    var errorDescription: String? {
        switch self {
        case .invalidConnection: return "Paste the connection code from your PC. Both devices must use the same Wi-Fi."
        case .rejected: return "The PC rejected the request. Check the connection and the PC service."
        case .invalidCapture: return "This scan cannot be submitted. Export it for inspection on your PC."
        case .failed(let message): return message
        }
    }
}

private final class PCPinnedSession: NSObject, URLSessionDelegate, URLSessionTaskDelegate, @unchecked Sendable {
    let pin: String
    init(pin: String) { self.pin = pin }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let certificate = SecTrustGetCertificateAtIndex(trust, 0) else {
            completionHandler(.cancelAuthenticationChallenge, nil); return
        }
        let data = SecCertificateCopyData(certificate) as Data
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard digest == pin else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

@MainActor
final class PCReconstructionClient: ObservableObject {
    @Published var configured = false
    @Published var busy = false
    @Published var message = "Connect your PC to create a splat."
    @Published var completedModel: URL?
    private var connection: PCConnection?
    private let keyAccount = "CaptureSplat-PC-Reconstruction"

    init() {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                  kSecAttrAccount as String: keyAccount,
                                  kSecReturnData as String: true]
        var result: CFTypeRef?
        if SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
           let data = result as? Data, let code = String(data: data, encoding: .utf8),
           let value = try? PCConnection.parse(code) {
            connection = value; configured = true; message = "PC connected."
        }
    }

    func connect(code: String) async {
        guard !busy else { return }
        busy = true; defer { busy = false }
        do {
            let value = try PCConnection.parse(code)
            let session = makeSession(value)
            defer { session.invalidateAndCancel() }
            _ = try await request(session, value, path: "/v1/health")
            let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                      kSecAttrAccount as String: keyAccount]
            SecItemDelete(query as CFDictionary)
            var item = query
            item[kSecValueData as String] = Data(code.trimmingCharacters(in: .whitespacesAndNewlines).utf8)
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw PCError.rejected }
            connection = value; configured = true; message = "PC connected. Ready to reconstruct."
        } catch { message = error.localizedDescription }
    }

    private func makeSession(_ value: PCConnection) -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 2400
        config.waitsForConnectivity = true
        return URLSession(configuration: config, delegate: PCPinnedSession(pin: value.pin), delegateQueue: nil)
    }

    private func request(_ session: URLSession, _ connection: PCConnection, path: String,
                         method: String = "GET", body: Data? = nil) async throws -> [String: Any] {
        guard let url = URL(string: connection.url + path) else { throw PCError.invalidConnection }
        var req = URLRequest(url: url)
        req.httpMethod = method; req.httpBody = body
        req.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
        if body != nil { req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, response) = try await session.data(for: req)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              data.count <= 1024 * 1024,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { throw PCError.rejected }
        return object
    }

    func reconstruct(directory: URL) async {
        guard !busy, let connection else { return }
        busy = true; completedModel = nil
        defer { busy = false }
        let session = makeSession(connection)
        defer { session.invalidateAndCancel() }
        do {
            message = "Preparing scan…"
            let manifestURL = directory.appendingPathComponent("capture.json")
            let size = try manifestURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size > 0, size <= 16 * 1024 * 1024 else { throw PCError.invalidCapture }
            let body = try Data(contentsOf: manifestURL)
            var state = try await request(session, connection, path: "/v1/scans", method: "POST", body: body)
            guard let id = state["id"] as? String,
                  id.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                  let images = state["images"] as? [String], images.count <= 512
            else { throw PCError.rejected }
            if state["status"] as? String == "uploading" {
                let uploaded = state["uploaded"] as? [String: Any] ?? [:]
                for (index, image) in images.enumerated() {
                    if uploaded[String(index)] != nil { continue }
                    guard image.range(of: "^rgb/[A-Za-z0-9_.-]{1,100}$", options: .regularExpression) != nil
                    else { throw PCError.invalidCapture }
                    let source = directory.appendingPathComponent(image)
                    let imageSize = try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                    guard imageSize > 0, imageSize <= 25 * 1024 * 1024 else { throw PCError.invalidCapture }
                    let data = try Data(contentsOf: source, options: .mappedIfSafe)
                    let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                    message = "Sending scan: \(index + 1) of \(images.count) images"
                    var req = URLRequest(url: URL(string: connection.url + "/v1/scans/\(id)/images/\(index)")!)
                    req.httpMethod = "PUT"
                    req.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
                    req.setValue(digest, forHTTPHeaderField: "X-Content-SHA256")
                    let (_, response) = try await session.upload(for: req, fromFile: source)
                    guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw PCError.rejected }
                }
                state = try await request(session, connection, path: "/v1/scans/\(id)/run", method: "POST")
            }
            while state["status"] as? String == "processing" {
                message = "Reconstructing on your PC… Keep the app open."
                try await Task.sleep(nanoseconds: 5_000_000_000)
                state = try await request(session, connection, path: "/v1/scans/\(id)")
            }
            guard state["status"] as? String == "completed",
                  let digest = state["result_sha256"] as? String,
                  let bytes = state["result_bytes"] as? Int, bytes > 0, bytes <= 64 * 1024 * 1024
            else { throw PCError.failed(state["message"] as? String ?? "Reconstruction failed.") }
            message = "Downloading splat…"
            var req = URLRequest(url: URL(string: connection.url + "/v1/scans/\(id)/model")!)
            req.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
            let (temporary, response) = try await session.download(for: req)
            defer { try? FileManager.default.removeItem(at: temporary) }
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw PCError.rejected }
            let data = try Data(contentsOf: temporary, options: .mappedIfSafe)
            guard data.count == bytes,
                  SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == digest
            else { throw PCError.rejected }
            let source = temporary.appendingPathExtension("splat")
            try FileManager.default.copyItem(at: temporary, to: source)
            defer { try? FileManager.default.removeItem(at: source) }
            let model = try SplatLibrary.importModel(source)
            // Pinned ReSplat demo exports in a context-view frame with +Z forward.
            let camera: [String: Any] = ["id": 0, "width": 1920, "height": 1440, "fx": 1100, "fy": 1100,
                                        "position": [0, 0, 0], "rotation": [[1,0,0],[0,1,0],[0,0,1]]]
            try JSONSerialization.data(withJSONObject: camera).write(to: model.appendingPathExtension("camera.json"), options: .atomic)
            completedModel = model
            message = "Splat ready. Reconstruction quality has not been verified."
        } catch { message = error.localizedDescription }
    }
}

struct PCReconstructionView: View {
    @ObservedObject var client: PCReconstructionClient
    let directory: URL?
    let openModel: (URL) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var code = ""
    var body: some View {
        NavigationStack {
            Form {
                if !client.configured {
                    Section("Connect this PC") {
                        SecureField("Paste PC connection code", text: $code)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        Button("Connect") { Task { await client.connect(code: code); code = "" } }
                            .disabled(client.busy || code.isEmpty)
                        Text("Use the connection code shown by your PC. Keep both devices on the same Wi-Fi.")
                    }
                }
                Section {
                    Text(client.message)
                    if client.busy { ProgressView() }
                    if client.configured, let directory {
                        Button("Create splat on PC") { Task { await client.reconstruct(directory: directory) } }
                            .disabled(client.busy)
                        Text("Sends this saved scan’s images and cameras to your PC. Keep this app open until the result downloads.")
                    }
                    if let model = client.completedModel {
                        Button("Open room splat") { openModel(model) }
                    }
                }
            }
            .navigationTitle("PC reconstruction")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
            .interactiveDismissDisabled(client.busy)
        }
    }
}
