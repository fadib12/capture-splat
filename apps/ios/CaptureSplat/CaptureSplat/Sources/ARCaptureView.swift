import ARKit
import RealityKit
import SwiftUI

struct ARCaptureView: UIViewRepresentable {
    @EnvironmentObject private var capture: CaptureController

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        capture.attach(session: view.session)

        context.coordinator.update(view: view, capture: capture)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.update(view: uiView, capture: capture)
    }

    static func dismantleUIView(_ uiView: ARView, coordinator: Coordinator) {
        coordinator.clear(view: uiView)
        uiView.session.pause()
    }

    final class Coordinator {
        private let renderer = SpatialGuidanceRenderer()
        private var lastUsesCaptureSensors: Bool?
        private var lastUsesDepth: Bool?
        private var lastUsesCaptureVideoFormat: Bool?
        private var captureVideoFormat: ARConfiguration.VideoFormat?

        func update(view: ARView, capture: CaptureController) {
            // RoomPlan owns the shared AR configuration while scanning and
            // finalizing. A SwiftUI refresh must not replace its configuration.
            if capture.isRoomSemanticsActive {
                renderer.update(view: view, capture: capture)
                return
            }
            let policy = capture.runtimePolicy
            let usesCaptureVideoFormat = capture.isStarting || capture.isRecording || capture.isFinalizing
            if lastUsesCaptureSensors != policy.usesCaptureSensors
                || lastUsesDepth != policy.usesDepth
                || lastUsesCaptureVideoFormat != usesCaptureVideoFormat {
                let configuration = ARWorldTrackingConfiguration()
                configuration.worldAlignment = .gravity
                let rearFormats = ARWorldTrackingConfiguration.supportedVideoFormats
                    .filter { $0.captureDevicePosition == .back }
                let fourKFormats = rearFormats.filter { format in
                    max(format.imageResolution.width, format.imageResolution.height) >= 3840
                        && min(format.imageResolution.width, format.imageResolution.height) >= 2160
                }
                if usesCaptureVideoFormat {
                    // Lock one supported format for the whole recording and finalization.
                    // The video writer's dimensions must not change under thermal pressure.
                    if captureVideoFormat == nil {
                        captureVideoFormat = preferredCaptureFormat(
                            rearFormats: rearFormats,
                            fourKFormats: fourKFormats,
                            defaultFormat: configuration.videoFormat
                        )
                    }
                    if let captureVideoFormat { configuration.videoFormat = captureVideoFormat }
                } else {
                    // ARKit's default preview keeps idle sensor/encoder work lower.
                    captureVideoFormat = nil
                }
                capture.selectCaptureCamera(
                    configuration.videoFormat,
                    forCapture: usesCaptureVideoFormat,
                    fourKSupported: !fourKFormats.isEmpty
                )
                if policy.usesDepth,
                   ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
                    configuration.frameSemantics.insert(.sceneDepth)
                }
                if policy.usesCaptureSensors {
                    if ARWorldTrackingConfiguration.supportsFrameSemantics(.personSegmentationWithDepth) {
                        configuration.frameSemantics.insert(.personSegmentationWithDepth)
                    }
                    if ARWorldTrackingConfiguration.supportsSceneReconstruction(.meshWithClassification) {
                        configuration.sceneReconstruction = .meshWithClassification
                    } else if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
                        configuration.sceneReconstruction = .mesh
                    }
                    configuration.planeDetection = [.horizontal, .vertical]
                }
                // Keep the same world tracking frame when enabling capture features.
                // No resetTracking or removeExistingAnchors option is used.
                view.session.run(configuration)
                lastUsesCaptureSensors = policy.usesCaptureSensors
                lastUsesDepth = policy.usesDepth
                lastUsesCaptureVideoFormat = usesCaptureVideoFormat
            }
            capture.startRoomSemanticsIfReady()
            renderer.update(view: view, capture: capture)
        }

        private func preferredCaptureFormat(
            rearFormats: [ARConfiguration.VideoFormat],
            fourKFormats: [ARConfiguration.VideoFormat],
            defaultFormat: ARConfiguration.VideoFormat
        ) -> ARConfiguration.VideoFormat {
            // Only use a physical ultra-wide camera exposed by this ARKit
            // configuration; never open a separate camera and lose calibration.
            if let ultraWide4K = fourKFormats
                .filter({ $0.captureDeviceType == .builtInUltraWideCamera })
                .min(by: { $0.framesPerSecond < $1.framesPerSecond }) {
                return ultraWide4K
            }
            if #available(iOS 16.0, *),
               let recommended = ARWorldTrackingConfiguration.recommendedVideoFormatFor4KResolution,
               let supported = fourKFormats.first(where: {
                   $0.captureDeviceType == recommended.captureDeviceType
                       && $0.imageResolution == recommended.imageResolution
                       && $0.framesPerSecond == recommended.framesPerSecond
               }) {
                return supported
            }
            if let supported4K = fourKFormats.min(by: { $0.framesPerSecond < $1.framesPerSecond }) {
                return supported4K
            }
            return rearFormats
                .filter { $0.captureDeviceType == .builtInUltraWideCamera }
                .max { $0.imageResolution.width * $0.imageResolution.height
                    < $1.imageResolution.width * $1.imageResolution.height }
                ?? defaultFormat
        }

        func clear(view: ARView) {
            renderer.clear(view: view)
        }
    }

    final class SpatialGuidanceRenderer {
        private var lastShowsMesh = false
        private var lastShowsFeatures = false

        func update(view: ARView, capture: CaptureController) {
            let showsMesh = capture.isRecording && capture.spatialGuidanceShowsMesh
            let showsFeatures = capture.isRecording && capture.isSpatialGuidanceVisible
                && capture.spatialGuidanceFaceBudget > 0
                && !showsMesh
                && capture.spatialGuidanceMode != "depth_points"
            guard showsMesh != lastShowsMesh
                    || showsFeatures != lastShowsFeatures
                    || view.debugOptions.contains(.showSceneUnderstanding) != showsMesh
                    || view.debugOptions.contains(.showFeaturePoints) != showsFeatures else {
                return
            }
            lastShowsMesh = showsMesh
            lastShowsFeatures = showsFeatures
            if showsMesh {
                view.debugOptions.insert(.showSceneUnderstanding)
            } else {
                view.debugOptions.remove(.showSceneUnderstanding)
            }
            if showsFeatures {
                view.debugOptions.insert(.showFeaturePoints)
            } else {
                view.debugOptions.remove(.showFeaturePoints)
            }
        }

        func clear(view: ARView) {
            view.debugOptions.remove(.showSceneUnderstanding)
            view.debugOptions.remove(.showFeaturePoints)
        }
    }
}
