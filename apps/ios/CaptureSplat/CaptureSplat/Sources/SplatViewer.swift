import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers
import WebKit

enum SplatLibrary {
    static var directory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("TrainedSplats", isDirectory: true)
    }

    static var latest: URL? {
        guard let name = UserDefaults.standard.string(forKey: "latestTrainedSplat"),
              ["splat", "ply"].contains((name as NSString).pathExtension),
              UUID(uuidString: (name as NSString).deletingPathExtension) != nil else { return nil }
        let url = directory.appendingPathComponent(name)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    static func importModel(_ source: URL) throws -> URL {
        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }
        let format = source.pathExtension.lowercased()
        guard ["splat", "ply"].contains(format) else {
            throw NSError(domain: "CaptureSplat", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Choose a trained .ply or .splat file exported by the desktop trainer."])
        }
        let size = try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, size <= 64 * 1024 * 1024 else {
            throw NSError(domain: "CaptureSplat", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "This splat is empty, invalid, or exceeds the 64 MB mobile limit."])
        }
        let data = try Data(contentsOf: source, options: .mappedIfSafe)
        guard data.count == size else { throw CocoaError(.fileReadCorruptFile) }
        let valid = format == "ply" ? validateGaussianPLY(data) : validateCompactSplat(data)
        guard valid else { throw CocoaError(.fileReadCorruptFile) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let name = UUID().uuidString + "." + format
        let target = directory.appendingPathComponent(name)
        try data.write(to: target, options: .atomic)
        UserDefaults.standard.set(name, forKey: "latestTrainedSplat")
        return target
    }

    private static func validateCompactSplat(_ data: Data) -> Bool {
        guard data.count.isMultiple(of: 32) else { return false }
        return data.withUnsafeBytes { bytes in
            for offset in stride(from: 0, to: bytes.count, by: 32) {
                for field in 0..<6 {
                    let value = bytes.loadUnaligned(fromByteOffset: offset + field * 4, as: Float.self)
                    if !value.isFinite || (field >= 3 && value <= 0) { return false }
                }
                if (28..<32).allSatisfy({ bytes[offset + $0] == 128 }) { return false }
            }
            return true
        }
    }

    /// Accept only the bounded binary float Gaussian contract produced by our trainer.
    /// Mesh PLYs, arbitrary lists/types, trailing bytes and non-finite values are rejected.
    private static func validateGaussianPLY(_ data: Data) -> Bool {
        let prefix = Data(data.prefix(16 * 1024))
        let marker = prefix.range(of: Data("end_header\n".utf8))
            ?? prefix.range(of: Data("end_header\r\n".utf8))
        guard let marker,
              let header = String(data: data.prefix(marker.upperBound), encoding: .utf8) else { return false }
        let lines = header.components(separatedBy: "\n").map {
            $0.trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { !$0.isEmpty }
        guard lines.count <= 100, lines.first == "ply", lines.last == "end_header" else { return false }
        var vertexCount: Int?
        var formatSeen = false
        var properties: [String] = []
        let base = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
                    "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
        let optional = Set(["nx", "ny", "nz"] + (0..<45).map { "f_rest_\($0)" })
        let allowed = Set(base).union(optional)
        for line in lines.dropFirst().dropLast() {
            let fields = line.split(whereSeparator: \.isWhitespace).map(String.init)
            guard let first = fields.first else { continue }
            switch first {
            case "comment", "obj_info": continue
            case "format":
                guard !formatSeen, vertexCount == nil,
                      fields == ["format", "binary_little_endian", "1.0"] else { return false }
                formatSeen = true
            case "element":
                guard formatSeen, vertexCount == nil, fields.count == 3, fields[1] == "vertex",
                      fields[2].allSatisfy(\.isNumber), let count = Int(fields[2]),
                      count > 0, count <= 2_000_000 else { return false }
                vertexCount = count
            case "property":
                guard vertexCount != nil, fields.count == 3,
                      ["float", "float32"].contains(fields[1]), allowed.contains(fields[2]),
                      !properties.contains(fields[2]) else { return false }
                properties.append(fields[2])
            default: return false
            }
        }
        guard let vertexCount, Set(base).isSubset(of: Set(properties)) else { return false }
        let shCount = properties.filter { $0.hasPrefix("f_rest_") }.count
        guard [0, 9, 24, 45].contains(shCount),
              (0..<shCount).allSatisfy({ properties.contains("f_rest_\($0)") }) else { return false }
        let stride = properties.count * 4
        guard data.count == marker.upperBound + vertexCount * stride else { return false }
        let rotationOffsets = (0..<4).compactMap { properties.firstIndex(of: "rot_\($0)") }
        let scaleOffsets = (0..<3).compactMap { properties.firstIndex(of: "scale_\($0)") }
        return data.withUnsafeBytes { bytes in
            for row in 0..<vertexCount {
                let start = marker.upperBound + row * stride
                for field in properties.indices {
                    if !bytes.loadUnaligned(fromByteOffset: start + field * 4, as: Float.self).isFinite { return false }
                }
                var norm: Double = 0
                for field in rotationOffsets {
                    let value = Double(bytes.loadUnaligned(fromByteOffset: start + field * 4, as: Float.self))
                    norm += value * value
                }
                guard norm > 1e-16, norm.isFinite else { return false }
                for field in scaleOffsets {
                    let value = bytes.loadUnaligned(fromByteOffset: start + field * 4, as: Float.self)
                    guard value >= -30, value <= 20 else { return false }
                }
            }
            return true
        }
    }
}

struct SplatFilePicker: UIViewControllerRepresentable {
    let completion: (Result<URL?, Error>) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(completion: completion) }
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.data, .item], asCopy: true)
        picker.allowsMultipleSelection = false
        picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let completion: (Result<URL?, Error>) -> Void
        init(completion: @escaping (Result<URL?, Error>) -> Void) { self.completion = completion }
        func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { completion(.success(nil)) }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            guard let url = urls.first else { completion(.success(nil)); return }
            DispatchQueue.global(qos: .userInitiated).async {
                let result = Result<URL?, Error> { try SplatLibrary.importModel(url) }
                DispatchQueue.main.async { self.completion(result) }
            }
        }
    }
}

/// Serves only the bundled viewer and one explicitly imported model. No remote URLs.
private final class LocalSplatResources: NSObject, WKURLSchemeHandler {
    let model: URL
    private let lock = NSLock()
    private var stopped: Set<ObjectIdentifier> = []
    init(model: URL) { self.model = model }

    private func worldCameraScript(_ camera: [String: Any]) -> String? {
        guard camera["convention"] as? String == "opengl",
              let rotation = camera["rotation"] as? [[Double]], rotation.count == 3,
              rotation.allSatisfy({ $0.count == 3 && $0.allSatisfy { $0.isFinite } }),
              let position = camera["position"] as? [Double], position.count == 3,
              position.allSatisfy({ $0.isFinite && abs($0) < 1e6 }) else { return nil }
        for row in 0..<3 {
            for column in 0..<3 {
                let dot = (0..<3).reduce(0.0) { $0 + rotation[row][$1] * rotation[column][$1] }
                if abs(dot - (row == column ? 1 : 0)) > 1e-3 { return nil }
            }
        }
        let determinant = rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
            - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
            + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
        guard determinant > 0.999 else { return nil }
        let value: [String: Any] = ["convention": "opengl", "rotation": rotation,
                                    "position": position, "gravity_up": [0, 1, 0]]
        guard let data = try? JSONSerialization.data(withJSONObject: value),
              let json = String(data: data, encoding: .utf8) else { return nil }
        return "window.CAPTURE_SPLAT_CAMERA=\(json);"
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {
        lock.lock(); stopped.insert(ObjectIdentifier(urlSchemeTask)); lock.unlock()
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let identifier = ObjectIdentifier(task)
        lock.lock(); stopped.remove(identifier); lock.unlock()
        guard let request = task.request.url, request.host == "viewer" else {
            task.didFailWithError(URLError(.unsupportedURL)); return
        }
        let file: URL?
        let mime: String
        switch request.path {
        case "/index.html":
            file = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "SplatViewer")
            mime = "text/html"
        case "/main.js":
            file = Bundle.main.url(forResource: "main", withExtension: "js", subdirectory: "SplatViewer")
            mime = "application/javascript"
        case "/model.splat" where model.pathExtension.lowercased() == "splat":
            file = model; mime = "application/octet-stream"
        case "/model.ply" where model.pathExtension.lowercased() == "ply":
            file = model; mime = "application/octet-stream"
        default: task.didFailWithError(URLError(.fileDoesNotExist)); return
        }
        guard let file else { task.didFailWithError(URLError(.fileDoesNotExist)); return }
        do {
            var data = try Data(contentsOf: file, options: .mappedIfSafe)
            if request.path == "/index.html", let html = String(data: data, encoding: .utf8) {
                // Inject only fixed format/camera constants, never sidecar strings or file paths.
                let format = model.pathExtension.lowercased() == "ply" ? "ply" : "splat"
                var script = "<script>window.CAPTURE_SPLAT_MODEL={url:'model.\(format)',format:'\(format)'};"
                let cameraURL = model.appendingPathExtension("camera.json")
                if let cameraSize = try? cameraURL.resourceValues(forKeys: [.fileSizeKey]).fileSize,
                   cameraSize > 0, cameraSize < 2048,
                   let cameraData = try? Data(contentsOf: cameraURL),
                   cameraData.count < 2048,
                   let camera = try? JSONSerialization.jsonObject(with: cameraData) as? [String: Any] {
                    if let worldScript = worldCameraScript(camera) {
                        script += worldScript
                    } else if (camera["rotation"] as? [[Int]]) == [[1,0,0],[0,1,0],[0,0,1]], camera["convention"] == nil {
                        script += "window.CAPTURE_SPLAT_CAMERA={rotation:[[1,0,0],[0,1,0],[0,0,1]]};"
                        if let gravity = camera["gravity_up"] as? [Double], gravity.count == 3,
                           gravity.allSatisfy({ $0.isFinite }) {
                            let norm = sqrt(gravity.reduce(0) { $0 + $1 * $1 })
                            if norm.isFinite, norm > 1e-6, norm < 1e6,
                               let gravityData = try? JSONSerialization.data(withJSONObject: gravity.map { $0 / norm }),
                               let gravityJSON = String(data: gravityData, encoding: .utf8) {
                                script += "window.CAPTURE_SPLAT_CAMERA.gravity_up=\(gravityJSON);"
                            }
                        }
                    }
                }
                script += "</script>"
                data = Data(html.replacingOccurrences(of: "<script src=\"main.js\"", with: script + "<script src=\"main.js\"").utf8)
            }
            guard data.count <= 64 * 1024 * 1024 else { throw CocoaError(.fileReadTooLarge) }
            let response = HTTPURLResponse(url: request, statusCode: 200, httpVersion: "HTTP/1.1",
                                           headerFields: ["Content-Type": mime, "Content-Length": String(data.count),
                                                          "Access-Control-Allow-Origin": "*"])!
            lock.lock(); let cancelled = stopped.contains(identifier); lock.unlock()
            guard !cancelled else { return }
            task.didReceive(response)
            task.didReceive(data)
            task.didFinish()
        } catch { task.didFailWithError(error) }
    }
}

struct TrainedSplatViewer: UIViewRepresentable {
    let model: URL

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.setURLSchemeHandler(LocalSplatResources(model: model), forURLScheme: "capturesplat")
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.isOpaque = false
        view.backgroundColor = .black
        view.scrollView.isScrollEnabled = false
        view.scrollView.bounces = false
        view.scrollView.alwaysBounceVertical = false
        view.scrollView.alwaysBounceHorizontal = false
        view.load(URLRequest(url: URL(string: "capturesplat://viewer/index.html")!))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
    static func dismantleUIView(_ view: WKWebView, coordinator: ()) {
        view.stopLoading()
        view.loadHTMLString("", baseURL: nil)
    }
}
