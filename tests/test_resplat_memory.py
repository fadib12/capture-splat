import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from capture_splat.resplat_memory import chunked_cost_volume, install_chunked_cost_volume


class SourceGateTests(unittest.TestCase):
    def test_pinned_external_boundary_is_replaced_once(self):
        source = Path(__file__).resolve().parents[1] / 'external/resplat/src/model/encoder/unimatch/mv_unimatch.py'
        if not source.is_file():
            self.skipTest('Requires the pinned user-installed external model.')
        module = SimpleNamespace(__file__=str(source), MultiViewUniMatch=type('Encoder', (), {}),
                                 warp_with_pose_depth_candidates=lambda *args, **kwargs: None)
        statistics = install_chunked_cost_volume(module)
        self.assertIn('_capture_splat_chunked_cost_volume', module.MultiViewUniMatch.forward.__code__.co_names)
        self.assertEqual(statistics['cost_volume_calls'], 0)

    def test_changed_external_source_is_rejected_before_execution(self):
        with patch('capture_splat.resplat_memory.Path.stat', return_value=SimpleNamespace(st_size=4)), \
                patch('capture_splat.resplat_memory.Path.read_bytes', return_value=b'pass'):
            with self.assertRaisesRegex(ValueError, 'source bytes changed'):
                install_chunked_cost_volume(SimpleNamespace(__file__='external.py'))


class CostVolumeParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import torch
        except ImportError:
            raise unittest.SkipTest('Requires the isolated Torch model runtime.')
        cls.torch = torch
        root = Path(__file__).resolve().parents[1]
        path = root / 'external/resplat/src/model/encoder/unimatch/matching.py'
        if not path.is_file():
            raise unittest.SkipTest('Requires the pinned user-installed external model.')
        spec = importlib.util.spec_from_file_location('test_external_resplat_matching', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.warp = staticmethod(module.warp_with_pose_depth_candidates)

    def test_ragged_depth_and_pair_chunks_match_full_correlation(self):
        torch = self.torch
        generator = torch.Generator().manual_seed(416)
        batch, pairs, channels, depths, height, width = 2, 3, 5, 7, 4, 6
        ref = torch.randn(batch, channels, height, width, generator=generator)
        target = torch.randn(batch, pairs, channels, height, width, generator=generator)
        intrinsics = torch.eye(3).repeat(batch, pairs, 1, 1)
        intrinsics[..., 0, 0] = intrinsics[..., 1, 1] = 3
        pose = torch.eye(4).repeat(batch, pairs, 1, 1)
        pose[..., :3, 3] = torch.randn(batch, pairs, 3, generator=generator)*.1
        candidates = .5 + torch.rand(batch*pairs, depths, height, width, generator=generator)
        for log_depth in (False, True):
            depth = candidates.exp() if log_depth else 1.0/candidates
            with torch.no_grad():
                warped = self.warp(target.reshape(-1, channels, height, width),
                                   intrinsics.reshape(-1, 3, 3), pose.reshape(-1, 4, 4), depth)
                warped = warped.reshape(batch, pairs, channels, depths, height, width)
                expected = ((ref[:, None, :, None]*warped).sum(2)/channels**.5).mean(1)
                for depth_chunk, pair_batch in ((1, 1), (3, 4), (8, 32)):
                    with self.subTest(log_depth=log_depth, depth_chunk=depth_chunk, pair_batch=pair_batch):
                        actual = chunked_cost_volume(ref, target, intrinsics, pose, candidates,
                                                     log_depth, False, warp=self.warp,
                                                     depth_chunk=depth_chunk, pair_batch=pair_batch)
                        torch.testing.assert_close(actual, expected, atol=1e-6, rtol=2e-6)


if __name__ == '__main__':
    unittest.main()
