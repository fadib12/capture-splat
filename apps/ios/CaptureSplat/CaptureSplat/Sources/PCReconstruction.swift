import Combine
import CryptoKit
import Foundation
import Security
import SwiftUI
import VisionKit

private struct PCConnection: Codable {
    let url: String
    let pin: String
    let token: String

    static func parse(_ code: String) throws -> PCConnection {
        var code = code.trimmingCharacters(in: .whitespacesAndNewlines)
        if code.hasPrefix("capturesplat-pc:") { code = String(code.dropFirst("capturesplat-pc:".count)) }
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

private struct PCScanAsset: Sendable {
    let path: String
    let url: URL
    let bytes: Int
    let sha256: String
}

private enum PCFullCapture {
    static let chunkBytes = 4 * 1024 * 1024

    static func prepare(_ directory: URL) throws -> (Data, [PCScanAsset]) {
        let manager = FileManager.default
        let keys: [URLResourceKey] = [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey, .fileSizeKey]
        guard let enumerator = manager.enumerator(at: directory, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles])
        else { throw PCError.invalidCapture }
        let prefix = directory.standardizedFileURL.path + "/"
        var assets: [PCScanAsset] = []
        var total = 0
        var entries = 0
        var names = Set<String>()
        for case let url as URL in enumerator {
            entries += 1
            guard entries <= 8192, url.standardizedFileURL.path.hasPrefix(prefix) else { throw PCError.invalidCapture }
            let values = try url.resourceValues(forKeys: Set(keys))
            guard values.isSymbolicLink != true else { throw PCError.invalidCapture }
            let path = String(url.standardizedFileURL.path.dropFirst(prefix.count))
            guard path.count <= 240, path.split(separator: "/").count <= 6 else { throw PCError.invalidCapture }
            if values.isDirectory == true { continue }
            guard values.isRegularFile == true, let size = values.fileSize, size > 0, size <= 2 * 1024 * 1024 * 1024,
                  path.range(of: "^[A-Za-z0-9_./-]+$", options: .regularExpression) != nil,
                  names.insert(path.lowercased()).inserted else { throw PCError.invalidCapture }
            total += size
            guard total <= 4 * 1024 * 1024 * 1024, assets.count < 4096 else { throw PCError.invalidCapture }
            let handle = try FileHandle(forReadingFrom: url)
            var hasher = SHA256()
            var readBytes = 0
            do {
                while let data = try handle.read(upToCount: chunkBytes), !data.isEmpty {
                    readBytes += data.count
                    guard readBytes <= size else { throw PCError.invalidCapture }
                    hasher.update(data: data)
                }
                try handle.close()
            } catch { try? handle.close(); throw error }
            guard readBytes == size else { throw PCError.invalidCapture }
            assets.append(PCScanAsset(path: path, url: url, bytes: size,
                                      sha256: hasher.finalize().map { String(format: "%02x", $0) }.joined()))
        }
        assets.sort { $0.path < $1.path }
        let manifest = try Data(contentsOf: directory.appendingPathComponent("capture.json"))
        guard manifest.count > 0, manifest.count <= 16 * 1024 * 1024 else { throw PCError.invalidCapture }
        let descriptor: [String: Any] = ["schema": "capture_splat.full_transfer.v1",
            "manifest_base64": manifest.base64EncodedString(),
            "files": assets.map { ["path": $0.path, "bytes": $0.bytes, "sha256": $0.sha256] as [String: Any] }]
        let body = try JSONSerialization.data(withJSONObject: descriptor, options: [.sortedKeys, .withoutEscapingSlashes])
        guard body.count <= 24 * 1024 * 1024 else { throw PCError.invalidCapture }
        return (body, assets)
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
              data.count <= 4 * 1024 * 1024,
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
            let health = try await request(session, connection, path: "/v1/health")
            guard (health["features"] as? [String] ?? []).contains("full_capture_v2") else {
                throw PCError.failed("Update the PC service to receive the complete scan, including video and LiDAR.")
            }
            message = "Preparing full scan..."
            let (body, assets) = try await Task.detached(priority: .userInitiated) {
                try PCFullCapture.prepare(directory)
            }.value
            var state = try await request(session, connection, path: "/v2/scans", method: "POST", body: body)
            guard let id = state["id"] as? String,
                  id.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                  let files = state["files"] as? [[String: Any]], files.count == assets.count,
                  let chunkBytes = state["chunk_bytes"] as? Int, chunkBytes == PCFullCapture.chunkBytes
            else { throw PCError.rejected }
            if state["status"] as? String == "uploading" {
                let uploaded = state["uploaded"] as? [String: Any] ?? [:]
                for (index, asset) in assets.enumerated() {
                    guard files[index]["path"] as? String == asset.path,
                          files[index]["sha256"] as? String == asset.sha256 else { throw PCError.rejected }
                    if uploaded[String(index)] != nil { continue }
                    let handle = try FileHandle(forReadingFrom: asset.url)
                    do {
                        var part = 0
                        var total = 0
                        var hasher = SHA256()
                        while let data = try handle.read(upToCount: chunkBytes), !data.isEmpty {
                            total += data.count
                            guard total <= asset.bytes else { throw PCError.invalidCapture }
                            hasher.update(data: data)
                            message = "Sending full scan: \(index + 1) of \(assets.count) files"
                            var req = URLRequest(url: URL(string: connection.url + "/v2/scans/\(id)/files/\(index)/chunks/\(part)")!)
                            req.httpMethod = "PUT"
                            req.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
                            req.setValue(SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(), forHTTPHeaderField: "X-Content-SHA256")
                            let (_, response) = try await session.upload(for: req, from: data)
                            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw PCError.rejected }
                            part += 1
                        }
                        try handle.close()
                        guard total == asset.bytes,
                              hasher.finalize().map({ String(format: "%02x", $0) }).joined() == asset.sha256
                        else { throw PCError.invalidCapture }
                    } catch { try? handle.close(); throw error }
                }
                state = try await request(session, connection, path: "/v2/scans/\(id)/run", method: "POST")
            }
            while state["status"] as? String == "processing" {
                message = "Reconstructing on your PC… Keep the app open."
                try await Task.sleep(nanoseconds: 5_000_000_000)
                state = try await request(session, connection, path: "/v2/scans/\(id)")
            }
            guard state["status"] as? String == "completed",
                  let digest = state["result_sha256"] as? String,
                  let bytes = state["result_bytes"] as? Int, bytes > 0, bytes <= 64 * 1024 * 1024
            else { throw PCError.failed(state["message"] as? String ?? "Reconstruction failed.") }
            message = "Downloading splat…"
            var req = URLRequest(url: URL(string: connection.url + "/v2/scans/\(id)/model")!)
            req.setValue("Bearer " + connection.token, forHTTPHeaderField: "Authorization")
            let (temporary, response) = try await session.download(for: req)
            defer { try? FileManager.default.removeItem(at: temporary) }
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw PCError.rejected }
            let data = try Data(contentsOf: temporary, options: .mappedIfSafe)
            guard data.count == bytes,
                  SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == digest
            else { throw PCError.rejected }
            let format = state["result_format"] as? String ?? "splat"
            guard ["splat", "ply"].contains(format) else { throw PCError.rejected }
            let source = temporary.appendingPathExtension(format)
            try FileManager.default.copyItem(at: temporary, to: source)
            defer { try? FileManager.default.removeItem(at: source) }
            let model = try await Task.detached(priority: .userInitiated) {
                try SplatLibrary.importModel(source)
            }.value
            // Camera is expressed in the exported context-view CV frame. The
            // viewer converts its basis once and uses the bound gravity vector.
            guard let camera = state["viewer_camera"] as? [String: Any],
                  JSONSerialization.isValidJSONObject(camera) else { throw PCError.rejected }
            let cameraData = try JSONSerialization.data(withJSONObject: camera)
            guard cameraData.count < 2048 else { throw PCError.rejected }
            try cameraData.write(to: model.appendingPathExtension("camera.json"), options: .atomic)
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
    @State private var scanning = false
    var body: some View {
        NavigationStack {
            Form {
                if !client.configured {
                    Section("Connect this PC") {
                        if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                            Button("Scan PC QR code", systemImage: "qrcode.viewfinder") { scanning = true }
                        }
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
                        Text("Sends this complete saved scan, including video, LiDAR and cameras, to your PC. Keep this app open until the result downloads.")
                    }
                    if let model = client.completedModel {
                        Button("Open room splat") { openModel(model) }
                    }
                }
            }
            .navigationTitle("PC reconstruction")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
            .interactiveDismissDisabled(client.busy)
            .onChange(of: client.completedModel) { _, model in
                if let model { openModel(model) }
            }
            .sheet(isPresented: $scanning) {
                LivePairingQRScanner(onInvitation: { invitation in
                    scanning = false
                    Task { await client.connect(code: invitation) }
                }, onFailure: { error in scanning = false; client.message = error }, acceptedPrefix: "capturesplat-pc:")
            }
        }
    }
}
