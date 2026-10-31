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
              UUID(uuidString: String(name.dropLast(6))) != nil,
              name.hasSuffix(".splat") else { return nil }
        let url = directory.appendingPathComponent(name)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    static func importModel(_ source: URL) throws -> URL {
        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }
        guard source.pathExtension.lowercased() == "splat" else {
            throw NSError(domain: "CaptureSplat", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Choose the trained .splat file exported by the desktop trainer."])
        }
        let size = try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, size <= 64 * 1024 * 1024, size.isMultiple(of: 32) else {
            throw NSError(domain: "CaptureSplat", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "This splat is empty, invalid, or exceeds the 64 MB mobile limit."])
        }
        let data = try Data(contentsOf: source, options: .mappedIfSafe)
        guard data.count == size else { throw CocoaError(.fileReadCorruptFile) }
        let valid = data.withUnsafeBytes { bytes in
            for offset in stride(from: 0, to: bytes.count, by: 32) {
                for field in 0..<6 {
                    let value = bytes.loadUnaligned(fromByteOffset: offset + field * 4, as: Float.self)
                    if !value.isFinite || (field >= 3 && value <= 0) { return false }
                }
                if (28..<32).allSatisfy({ bytes[offset + $0] == 128 }) { return false }
            }
            return true
        }
        guard valid else { throw CocoaError(.fileReadCorruptFile) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let name = UUID().uuidString + ".splat"
        let target = directory.appendingPathComponent(name)
        try data.write(to: target, options: .atomic)
        UserDefaults.standard.set(name, forKey: "latestTrainedSplat")
        return target
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
        case "/model.splat": file = model; mime = "application/octet-stream"
        default: task.didFailWithError(URLError(.fileDoesNotExist)); return
        }
        guard let file else { task.didFailWithError(URLError(.fileDoesNotExist)); return }
        do {
            let data = try Data(contentsOf: file, options: .mappedIfSafe)
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
        view.load(URLRequest(url: URL(string: "capturesplat://viewer/index.html")!))
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
    static func dismantleUIView(_ view: WKWebView, coordinator: ()) {
        view.stopLoading()
        view.loadHTMLString("", baseURL: nil)
    }
}
