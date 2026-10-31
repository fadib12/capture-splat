import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.test_capture_schema import make_capture


def load_script(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_brush_preserves_pose_and_per_frame_intrinsics_without_mutating_capture(tmp_path):
    adapter = load_script('prepare_brush_capture')
    capture = make_capture(tmp_path / 'capture')
    path = capture / 'capture.json'
    original = json.loads(path.read_text())
    second = json.loads(json.dumps(original['frames'][0]))
    second['rgb'] = 'rgb/second.jpg'
    second['intrinsics'] = dict(original['intrinsics'], fl_x=7, fl_y=8)
    second['transform_matrix'][0][3] = 0.3
    Image.new('RGB', (8, 6), 'blue').save(capture / 'rgb/second.jpg')
    original['frames'].append(second)
    path.write_text(json.dumps(original))
    source_bytes = path.read_bytes()
    result = adapter.prepare(capture, tmp_path / 'prepared')
    transforms = json.loads((Path(result['dataset']) / 'transforms.json').read_text())
    assert path.read_bytes() == source_bytes
    assert transforms['frames'][1]['transform_matrix'] == second['transform_matrix']
    assert transforms['frames'][1]['fl_x'] == 14
    assert transforms['frames'][1]['fl_y'] == 12
    assert transforms['frames'][0]['fl_x'] != transforms['frames'][1]['fl_x']


def test_brush_refuses_output_inside_source(tmp_path):
    adapter = load_script('prepare_brush_capture')
    capture = make_capture(tmp_path / 'capture')
    with pytest.raises(ValueError, match='immutable'):
        adapter.prepare(capture, capture / 'output')


def test_mobile_export_layout_and_invalid_values(tmp_path):
    pytest.importorskip('plyfile')
    from plyfile import PlyData, PlyElement
    exporter = load_script('export_mobile_splat')
    names = ['x', 'y', 'z', 'scale_0', 'scale_1', 'scale_2', 'opacity',
             'f_dc_0', 'f_dc_1', 'f_dc_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3']
    values = np.zeros(2, dtype=[(name, 'f4') for name in names])
    values['x'] = [1, 2]
    values['rot_0'] = 1
    source = tmp_path / 'input.ply'
    PlyData([PlyElement.describe(values, 'vertex')], text=False).write(source)
    target = tmp_path / 'room.splat'
    receipt = exporter.export(source, target)
    assert receipt['splat_count'] == 2 and target.stat().st_size == 64
    row = np.frombuffer(target.read_bytes()[:24], dtype='<f4')
    np.testing.assert_allclose(row, [1, 0, 0, 1, 1, 1])
    assert target.read_bytes()[28:32] == bytes([255, 128, 128, 128])
    assert receipt['higher_order_sh_preserved'] is False
    values['x'][0] = np.nan
    PlyData([PlyElement.describe(values, 'vertex')], text=False).write(source)
    with pytest.raises(ValueError, match='non-finite'):
        exporter.export(source, tmp_path / 'invalid.splat')
