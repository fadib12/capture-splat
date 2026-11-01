import Foundation
import CryptoKit

@main
struct FullCaptureTransferProbe {
    static func main() throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        defer { try? FileManager.default.removeItem(at:root) }
        try FileManager.default.createDirectory(at:root.appendingPathComponent("video"),withIntermediateDirectories:true)
        try Data("{}".utf8).write(to:root.appendingPathComponent("capture.json"))
        let video=Data(repeating:42,count:PCFullCapture.chunkBytes+17)
        try video.write(to:root.appendingPathComponent("video/capture.mov"))
        let (body,assets)=try PCFullCapture.prepare(root)
        let object=try JSONSerialization.jsonObject(with:body) as! [String:Any]
        precondition(object["schema"] as? String == "capture_splat.full_transfer.v1")
        precondition(assets.count==2)
        let asset=assets.first { $0.path=="video/capture.mov" }!
        precondition(asset.bytes==video.count)
        precondition(asset.sha256==SHA256.hash(data:video).map { String(format:"%02x",$0) }.joined())
        try FileManager.default.createSymbolicLink(at:root.appendingPathComponent("video/alias.mov"),withDestinationURL:root.appendingPathComponent("video/capture.mov"))
        do { _=try PCFullCapture.prepare(root); fatalError("Accepted a symlink") }
        catch { }
        print("PASS: full capture inventory, streaming multi-chunk SHA-256 and symlink rejection")
    }
}
