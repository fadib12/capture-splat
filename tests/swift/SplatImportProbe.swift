import Foundation

/// Compile against the actual SplatLibrary declaration extracted from
/// Sources/SplatViewer.swift; this probe does not duplicate its validator.
@main
struct SplatImportProbe {
    enum Failure: Error { case assertion(String) }

    static func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
        if !(try condition()) { throw Failure.assertion(message) }
    }

    static func main() throws {
        let manager = FileManager.default
        let sources = manager.temporaryDirectory.appendingPathComponent("splat-import-probe-" + UUID().uuidString)
        try manager.createDirectory(at: sources, withIntermediateDirectories: true)
        var imported: [URL] = []
        let previousLatest = UserDefaults.standard.object(forKey: "latestTrainedSplat")
        defer {
            for file in imported { try? manager.removeItem(at: file) }
            try? manager.removeItem(at: sources)
            if let previousLatest {
                UserDefaults.standard.set(previousLatest, forKey: "latestTrainedSplat")
            } else {
                UserDefaults.standard.removeObject(forKey: "latestTrainedSplat")
            }
        }

        let fields = ["x", "y", "z", "nx", "ny", "nz"]
            + (0..<3).map { "f_dc_\($0)" }
            + (0..<45).map { "f_rest_\($0)" }
            + ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
        var values = fields.map { _ in Float(0.01) }
        for field in ["scale_0", "scale_1", "scale_2"] { values[fields.firstIndex(of: field)!] = -2 }
        for field in ["rot_1", "rot_2", "rot_3"] { values[fields.firstIndex(of: field)!] = 0 }
        values[fields.firstIndex(of: "rot_0")!] = 1

        func ply(_ row: [Float], properties: [String]? = nil, vertexCount: String = "1",
                 format: String = "binary_little_endian", extraHeader: String = "") -> Data {
            let properties = properties ?? fields
            var header = "ply\nformat \(format) 1.0\nelement vertex \(vertexCount)\n"
            header += properties.map { "property float \($0)\n" }.joined()
            header += extraHeader + "end_header\n"
            var result = Data(header.utf8)
            for value in row {
                var bits = value.bitPattern.littleEndian
                withUnsafeBytes(of: &bits) { result.append(contentsOf: $0) }
            }
            return result
        }

        func write(_ bytes: Data, name: String, ext: String = "ply") throws -> URL {
            let url = sources.appendingPathComponent(name).appendingPathExtension(ext)
            try bytes.write(to: url)
            return url
        }

        func rejected(_ bytes: Data, _ name: String, ext: String = "ply") throws {
            let source = try write(bytes, name: name, ext: ext)
            do {
                let accepted = try SplatLibrary.importModel(source)
                imported.append(accepted)
            } catch {
                return
            }
            throw Failure.assertion("Accepted invalid input: \(name)")
        }

        let fullPLY = ply(values)
        let source = try write(fullPLY, name: "valid-full-sh3")
        let model = try SplatLibrary.importModel(source)
        imported.append(model)
        try require(model.pathExtension == "ply", "PLY suffix was lost")
        try require(try Data(contentsOf: model) == fullPLY, "PLY bytes changed")
        try require(SplatLibrary.latest == model, "latest did not resolve the imported PLY")

        try rejected(Data(fullPLY.dropLast()), "truncated")
        try rejected(fullPLY + Data([0]), "trailing-byte")
        try rejected(Data(), "empty")
        var bad = values
        bad[fields.firstIndex(of: "x")!] = .nan
        try rejected(ply(bad), "nan-position")
        bad = values
        bad[fields.firstIndex(of: "f_rest_44")!] = .infinity
        try rejected(ply(bad), "infinite-sh")
        bad = values
        for index in 0..<4 { bad[fields.firstIndex(of: "rot_\(index)")!] = 0 }
        try rejected(ply(bad), "zero-quaternion")
        bad = values
        bad[fields.firstIndex(of: "scale_2")!] = 21
        try rejected(ply(bad), "unsafe-scale")
        var duplicated = fields
        duplicated[duplicated.firstIndex(of: "z")!] = "x"
        try rejected(ply(values, properties: duplicated), "duplicate-property")
        var unknown = fields
        unknown[unknown.firstIndex(of: "nz")!] = "unrecognized"
        try rejected(ply(values, properties: unknown), "unknown-property")
        try rejected(ply(values, vertexCount: "2000001"), "vertex-limit")
        try rejected(ply(values, vertexCount: "-1"), "negative-count")
        try rejected(ply(values, format: "ascii"), "ascii-mesh")
        try rejected(ply(values, format: "binary_big_endian"), "big-endian")
        try rejected(ply(values, extraHeader: "comment " + String(repeating: "x", count: 17_000) + "\n"), "oversize-header")
        try rejected(ply(values, extraHeader: "element face 1\nproperty list uchar int vertex_indices\n"), "mesh-list")
        var incompleteSH = fields
        incompleteSH.remove(at: incompleteSH.firstIndex(of: "f_rest_44")!)
        try rejected(ply(values, properties: incompleteSH), "incomplete-sh-band")

        // The existing 32-byte compact format remains a supported fallback.
        var compact = Data()
        for value in [Float(0), 0, 2, 0.1, 0.1, 0.1] {
            var bits = value.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { compact.append(contentsOf: $0) }
        }
        compact.append(contentsOf: [255, 120, 40, 255, 255, 128, 128, 128])
        let compactSource = try write(compact, name: "valid-compact", ext: "splat")
        let compactModel = try SplatLibrary.importModel(compactSource)
        imported.append(compactModel)
        try require(SplatLibrary.latest == compactModel, "compact latest lookup failed")
        try require(try Data(contentsOf: compactModel) == compact, "compact bytes changed")
        try rejected(Data(compact.dropLast()), "truncated-compact", ext: "splat")
        compact.replaceSubrange(28..<32, with: [128, 128, 128, 128])
        try rejected(compact, "zero-compact-quaternion", ext: "splat")

        UserDefaults.standard.set("../outside.ply", forKey: "latestTrainedSplat")
        try require(SplatLibrary.latest == nil, "latest accepted a path escape")
        UserDefaults.standard.set("not-a-uuid.ply", forKey: "latestTrainedSplat")
        try require(SplatLibrary.latest == nil, "latest accepted a non-UUID model name")
        print("PASS: native full-SH3 PLY import, malformed-input rejection, compact fallback and latest lookup")
    }
}
