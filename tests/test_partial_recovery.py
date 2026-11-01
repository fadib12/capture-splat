import json
import os
from pathlib import Path
import pytest
from scripts.recover_partial_capture import read_index,inventory


def row(number=0):
    return {'video_frame_idx':number,'timestamp':number*.1,'camera_to_world':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
            'intrinsics':{'fl_x':500,'fl_y':500,'cx':320,'cy':240,'w':640,'h':480},'tracking_state':'normal'}


def test_exact_ordinal_pose_index_is_retained(tmp_path):
    path=tmp_path/'index.jsonl';rows=[row(0),row(1)]
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    assert read_index(path)==rows


@pytest.mark.parametrize('change',['duplicate','reflection','nonfinite','backwards_time'])
def test_recovery_rejects_ambiguous_or_invalid_pose_index(tmp_path,change):
    records=[row(0),row(1)]
    if change=='duplicate':records[1]['video_frame_idx']=0
    if change=='reflection':records[1]['camera_to_world'][0][0]=-1
    if change=='nonfinite':records[1]['intrinsics']['fl_x']=float('nan')
    if change=='backwards_time':records[1]['timestamp']=-1
    path=tmp_path/'index.jsonl';path.write_text('\n'.join(json.dumps(r) for r in records)+'\n')
    with pytest.raises(ValueError):read_index(path)


def test_inventory_binds_original_bytes_and_rejects_hardlink_alias(tmp_path):
    original=tmp_path/'a.bin';original.write_bytes(b'original evidence')
    record=inventory(tmp_path)
    assert record['a.bin']['bytes']==len(b'original evidence')
    try:os.link(original,tmp_path/'b.bin')
    except OSError:pytest.skip('Hard links unavailable on this filesystem')
    with pytest.raises(ValueError,match='alias'):inventory(tmp_path)
    assert original.read_bytes()==b'original evidence'


def test_portable_exports_preserves_each_frames_calibration(tmp_path):
    from scripts.recover_partial_capture import portable_exports
    from capture_splat.json_utils import write_json_strict,load_json_strict
    capture=tmp_path/'capture';capture.mkdir()
    write_json_strict(capture/'capture_splat_frames_summary.json',{'video':'machine-specific','output_dir':'machine-specific'})
    frames=[]
    for i,focal in enumerate((500,550)):
        r=row(i)
        frames.append({'rgb':f'images/{i+1:06d}.jpg','transform_matrix':r['camera_to_world'],
                       'intrinsics':dict(r['intrinsics'],fl_x=focal),'timestamp':r['timestamp'],'source_video_frame':i})
    portable_exports(capture,tmp_path,{'frames':frames})
    result=load_json_strict(capture/'transforms.json')
    assert 'fl_x' not in result
    assert [f['fl_x'] for f in result['frames']]==[500,550]
    assert result['frames'][0]['transform_matrix']==frames[0]['transform_matrix']
    summary=load_json_strict(capture/'capture_splat_frames_summary.json')
    assert summary['video']=='video/capture.mov' and summary['output_dir']=='.'
    assert not result['recovery_depth_available']
