from pathlib import Path

import numpy as np
import pytest

from capture_splat.json_utils import load_json_strict, write_json_strict
from capture_splat.resplat_adapter import inference_command, prepare, sha256
from capture_splat.rgbd_seed import quaternion_rotation
from tests.test_capture_schema import make_capture


def room(root):
    capture = make_capture(root)
    data = load_json_strict(capture / 'capture.json')
    frame = data['frames'][0]
    for i in range(1, 9):
        target = capture / 'rgb' / f'{i:06d}.jpg'
        if i > 1:
            target.write_bytes((capture / 'rgb/000001.jpg').read_bytes())
    data['frames'] = [dict(frame, rgb=f'rgb/{i:06d}.jpg') for i in range(1, 9)]
    write_json_strict(capture / 'capture.json', data)
    return capture


def test_prepares_bound_images_and_converts_camera_axes_once(tmp_path):
    capture = room(tmp_path / 'capture')
    before = sha256(capture / 'capture.json')
    out = tmp_path / 'prepared'
    receipt = prepare(capture, out)
    assert receipt['accepted_image_count'] == 8
    assert receipt['decision'] == 'hold'
    assert not receipt['model_runtime_executed']
    assert sha256(capture / 'capture.json') == before
    assert receipt['frames'][0]['sha256'] == sha256(out / 'scene/images/000001.jpg')
    lines = (out / 'scene/sparse/0/images.txt').read_text().splitlines()
    pose = next(line.split() for line in lines if line and not line.startswith('#'))
    rotation = quaternion_rotation(*map(float, pose[1:5]))
    np.testing.assert_allclose(rotation.T, np.diag([1, -1, -1]), atol=1e-10)


@pytest.mark.parametrize('bad_path', ['../outside.jpg', '/outside.jpg', 'C:/outside.jpg', 'rgb\\000001.jpg'])
def test_rejects_escaping_or_nonportable_image_paths(tmp_path, bad_path):
    capture = room(tmp_path / 'capture')
    data = load_json_strict(capture / 'capture.json')
    data['frames'][0]['rgb'] = bad_path
    write_json_strict(capture / 'capture.json', data)
    with pytest.raises(ValueError, match='relative'):
        prepare(capture, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_rejects_duplicate_images_before_copying(tmp_path):
    capture = room(tmp_path / 'capture')
    data = load_json_strict(capture / 'capture.json')
    data['frames'][1]['rgb'] = data['frames'][0]['rgb']
    write_json_strict(capture / 'capture.json', data)
    with pytest.raises(ValueError, match='Duplicate'):
        prepare(capture, tmp_path / 'out')


def test_immutable_input_and_existing_output_are_protected(tmp_path):
    capture = room(tmp_path / 'capture')
    with pytest.raises(ValueError, match='separate'):
        prepare(capture, capture / 'out')
    out = tmp_path / 'out'
    out.mkdir()
    marker = out / 'keep.txt'
    marker.write_text('keep')
    with pytest.raises(ValueError, match='fresh'):
        prepare(capture, out)
    assert marker.read_text() == 'keep'


def test_rejected_frames_do_not_silently_fill_context(tmp_path):
    capture = room(tmp_path / 'capture')
    data = load_json_strict(capture / 'capture.json')
    data['frames'][0]['accepted'] = False
    write_json_strict(capture / 'capture.json', data)
    with pytest.raises(ValueError, match='8 to 512'):
        prepare(capture, tmp_path / 'out')


def test_command_preserves_paths_as_argv_and_bounds_render_batch():
    command = inference_command(Path('external/space in path'), Path('scan inputs'), Path('new output'))
    assert command[1] == str(Path('external/space in path/scripts/infer_colmap.py'))
    assert command[command.index('--scene_path') + 1] == 'scan inputs'
    assert command[command.index('--render_chunk_size') + 1] == '1'
    assert command[command.index('--frame_distance') + 1] == '512'
    assert '--save_ply' in command
    assert '--no_eval' in command  # Runtime smoke is not a quality evaluation.
