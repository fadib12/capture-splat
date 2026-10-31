import Foundation

@main
struct PreviewReservoirProbe {
    static func main() {
        var preview = BoundedPreviewReservoir<Int>(capacity: 128)
        var chunked = BoundedPreviewReservoir<Int>(capacity: 128)
        let source = Array(0..<10_000)
        preview.append(contentsOf: source)
        for start in stride(from: 0, to: source.count, by: 100) {
            chunked.append(contentsOf: Array(source[start..<min(start + 100, source.count)]))
        }
        precondition(preview.values.count == 128)
        precondition(preview.totalSeen == 10_000)
        precondition(preview.values == chunked.values, "Checkpoint batching must not change sampling")
        precondition(Set(preview.values).count == 128)
        precondition(preview.values.allSatisfy { source.contains($0) })
        for third in 0..<3 {
            precondition(preview.values.contains { min($0 * 3 / source.count, 2) == third },
                         "Preview must retain early, middle, and late capture support")
        }
        preview.removeAll()
        precondition(preview.values.isEmpty && preview.totalSeen == 0)
        preview.append(contentsOf: [1, 2, 3])
        precondition(preview.values == [1, 2, 3])
        var empty = BoundedPreviewReservoir<Int>(capacity: 0)
        empty.append(contentsOf: source)
        precondition(empty.values.isEmpty && empty.totalSeen == 10_000)
        var single = BoundedPreviewReservoir<Int>(capacity: 1)
        single.append(contentsOf: source)
        precondition(single.values.count == 1 && single.totalSeen == 10_000)
        let phases: [CaptureRuntimePhase] = [.idle, .starting, .recording, .finalizing, .idle, .starting, .recording]
        let expectedVisible = [false, false, true, false, false, false, true]
        let expectedSensors = [false, true, true, true, false, true, true]
        for (index, phase) in phases.enumerated() {
            let policy = CaptureRuntimePolicy(phase: phase, needsDepthLock: false, guidanceRequested: true)
            precondition(policy.showsGuidance == expectedVisible[index], "Guidance must follow recording across repeated captures")
            precondition(policy.usesCaptureSensors == expectedSensors[index], "Capture sensors must remain active through finalization")
            precondition(policy.usesDepth == expectedSensors[index])
        }
        let lockPreview = CaptureRuntimePolicy(phase: .idle, needsDepthLock: true, guidanceRequested: true)
        precondition(lockPreview.usesDepth && !lockPreview.usesCaptureSensors && !lockPreview.showsGuidance)
        let cameraOnly = CaptureRuntimePolicy(phase: .recording, needsDepthLock: false, guidanceRequested: false)
        precondition(!cameraOnly.showsGuidance && cameraOnly.usesDepth && cameraOnly.usesCaptureSensors)
        print("Preview reservoir and repeated capture lifecycle checks passed")
    }
}
