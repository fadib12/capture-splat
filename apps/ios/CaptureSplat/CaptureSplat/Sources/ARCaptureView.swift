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

        func update(view: ARView, capture: CaptureController) {
            let policy = capture.runtimePolicy
            if lastUsesCaptureSensors != policy.usesCaptureSensors || lastUsesDepth != policy.usesDepth {
                let configuration = ARWorldTrackingConfiguration()
                configuration.worldAlignment = .gravity
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
            }
            renderer.update(view: view, capture: capture)
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
