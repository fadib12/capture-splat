import Foundation

@main
struct LosslessDownloadProbe {
    static func main() async throws {
        let port = CommandLine.arguments[1]
        let url = URL(string: "http://127.0.0.1:\(port)/model")!
        let (file, response) = try await URLSession.shared.download(from: url)
        defer { try? FileManager.default.removeItem(at: file) }
        precondition((response as? HTTPURLResponse)?.statusCode == 200)
        let expected = Data(String(repeating: "CaptureSplat lossless SH3 model transfer\n", count: 16384).utf8)
        let actual = try Data(contentsOf: file)
        precondition(actual == expected, "Foundation must expose original decoded model bytes")
        print("Lossless native download passed: \(actual.count) decoded bytes")
    }
}
