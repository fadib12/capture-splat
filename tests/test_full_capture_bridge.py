import base64
import hashlib
import json
from pathlib import Path
import pytest
from capture_splat.full_capture_bridge import FullCaptureStore,validate_descriptor,CHUNK_BYTES
from tests.test_prepare_capture import _capture


def package(tmp_path,video_bytes=b'video'):
    root=_capture(tmp_path/'source',count=8)
    (root/'video').mkdir();(root/'video/capture.mov').write_bytes(video_bytes)
    (root/'metadata').mkdir();(root/'metadata/frame_index.jsonl').write_text('{}\n')
    files=[]
    for path in sorted(root.rglob('*')):
        if path.is_file():
            content=path.read_bytes()
            files.append({'path':path.relative_to(root).as_posix(),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
    descriptor={'schema':'capture_splat.full_transfer.v1','manifest_base64':base64.b64encode((root/'capture.json').read_bytes()).decode(),'files':files}
    return root,descriptor,json.dumps(descriptor,sort_keys=True).encode()


def test_full_inventory_binds_video_depth_confidence_and_manifest(tmp_path):
    root,data,body=package(tmp_path)
    parsed,manifest=validate_descriptor(body)
    assert parsed['files']==data['files']
    assert manifest==(root/'capture.json').read_bytes()
    data['files']=[x for x in data['files'] if not x['path'].startswith('confidence/')]
    with pytest.raises(ValueError,match='missing|requires'):
        validate_descriptor(json.dumps(data).encode())


@pytest.mark.parametrize('name',['../private','rgb/../outside','rgb/A.','metadata/CON','metadata/com1.bin','video/other.transfer-partial','/rgb/a','rgb//a'])
def test_rejects_aliasing_and_escaping_paths(tmp_path,name):
    _,data,_=package(tmp_path)
    data['files'].append({'path':name,'bytes':1,'sha256':'0'*64})
    with pytest.raises(ValueError):validate_descriptor(json.dumps(data).encode())


def test_chunk_resume_is_bounded_and_requires_full_asset_digest(tmp_path):
    root,data,body=package(tmp_path,b'v'*(CHUNK_BYTES+3))
    store=FullCaptureStore(tmp_path/'scans',lambda _:None)
    state=store.create(body);identifier=state['id']
    assert store.create(body)['id']==identifier
    assert store.create(json.dumps(data,indent=2).encode())['id']==identifier
    with pytest.raises(ValueError,match='All images'):
        store.start(identifier)
    index=next(i for i,f in enumerate(data['files']) if f['path']=='video/capture.mov')
    chunks=[b'v'*CHUNK_BYTES,b'v'*3]
    with pytest.raises(ValueError,match='digest'):
        store.upload_chunk(identifier,index,0,chunks[0],'0'*64)
    for part in [1,1,0]:
        value=chunks[part]
        store.upload_chunk(identifier,index,part,value,hashlib.sha256(value).hexdigest())
    state=store.state(identifier)
    assert state['uploaded'][str(index)]['bytes']==CHUNK_BYTES+3
    assert (store.folder(identifier)/'capture/video/capture.mov').read_bytes()==b'v'*(CHUNK_BYTES+3)
    assert not (store.folder(identifier)/'chunks'/str(index)).exists()
    for i,binding in enumerate(data['files']):
        if str(i) in state['uploaded']:continue
        value=(root/binding['path']).read_bytes()
        store.upload_chunk(identifier,i,0,value,hashlib.sha256(value).hexdigest())
    store._verified_uploads(identifier,store.state(identifier))
    target=store.folder(identifier)/'capture/depth/depth_000001.npy'
    target.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='changed'):
        store._verified_uploads(identifier,store.state(identifier))


def test_v2_http_requires_authentication_and_preserves_v1(tmp_path):
    import threading
    from http.server import HTTPServer
    from capture_splat.pc_bridge import BridgeStore,handler
    from tests.test_pc_bridge import request
    root,data,body=package(tmp_path)
    legacy=BridgeStore(tmp_path/'legacy',lambda _:None)
    full=FullCaptureStore(tmp_path/'full',lambda _:None)
    full.active=legacy.active
    server=HTTPServer(('127.0.0.1',0),handler(legacy,'test-local-token',full_store=full))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    port=server.server_address[1]
    try:
        assert request(port,'/v2/scans','POST',body,authorized=False)[0]==401
        assert not list(full.root.iterdir())
        assert 'full_capture_v2' in request(port,'/v1/health')[1]['features']
        status,state=request(port,'/v2/scans','POST',body)
        assert status==200 and state['profile']=='rgbd'
        status,old=request(port,'/v1/scans','POST',(root/'capture.json').read_bytes())
        assert status==200 and old['profile']=='small'
        index=next(i for i,f in enumerate(data['files']) if f['path']=='video/capture.mov')
        value=b'video'
        assert request(port,f'/v2/scans/{state["id"]}/files/{index}/chunks/0','PUT',value,
                       **{'X-Content-SHA256':hashlib.sha256(value).hexdigest()})[0]==200
        assert request(port,f'/v2/scans/{state["id"]}')[1]['uploaded'][str(index)]['bytes']==len(value)
    finally:
        server.shutdown();server.server_close();thread.join()


def test_resumes_after_interrupted_assembly_or_rename(tmp_path):
    for stage in ['temporary','renamed']:
        case=tmp_path/stage;case.mkdir()
        root,data,body=package(case)
        store=FullCaptureStore(case/'scans',lambda _:None)
        state=store.create(body)
        index=next(i for i,f in enumerate(data['files']) if f['path']=='video/capture.mov')
        target=store.folder(state['id'])/'capture/video/capture.mov'
        target.parent.mkdir(parents=True)
        if stage=='temporary':target.with_name(target.name+'.transfer-partial').write_bytes(b'interrupted')
        else:target.write_bytes(b'video')
        store.upload_chunk(state['id'],index,0,b'video',hashlib.sha256(b'video').hexdigest())
        assert target.read_bytes()==b'video'
        assert str(index) in store.state(state['id'])['uploaded']

def test_completed_revision_upgrade_preserves_original_and_verified_uploads(tmp_path):
    from capture_splat.full_capture_bridge import REVISION
    root,data,body=package(tmp_path)
    store=FullCaptureStore(tmp_path/'scans',lambda _:None)
    state=store.create(body);identifier=state['id']
    for i,item in enumerate(data['files']):
        if item['path']=='capture.json':continue
        content=(root/item['path']).read_bytes()
        store.upload_chunk(identifier,i,0,content,hashlib.sha256(content).hexdigest())
    state=store.state(identifier)
    state.update(status='completed',pipeline_revision='gsplat-rgbd-v1',attempts_by_revision={'gsplat-rgbd-v1':1})
    store.save(identifier,state)
    result=store.folder(identifier)/'result';result.mkdir();(result/'original.txt').write_text('retained original')
    upgraded=store.create(body)
    assert upgraded['status']=='uploading' and upgraded['pipeline_revision']==REVISION
    assert len(upgraded['uploaded'])==len(data['files']) and upgraded['attempt']==2
    assert (store.folder(identifier)/'superseded-result-1/original.txt').read_text()=='retained original'
    assert not result.exists()
    assert store.create(body)['attempt']==2


def test_partial_old_revision_upload_still_resumes_without_mutation(tmp_path):
    root,data,body=package(tmp_path)
    store=FullCaptureStore(tmp_path/'scans',lambda _:None)
    state=store.create(body);identifier=state['id']
    state.update(pipeline_revision='gsplat-rgbd-v1',attempts_by_revision={'gsplat-rgbd-v1':1})
    store.save(identifier,state)
    resumed=store.create(body)
    assert resumed['uploaded']==state['uploaded'] and resumed['pipeline_revision']=='gsplat-rgbd-v1'
    with pytest.raises(ValueError,match='Missing capture asset'):store.start(identifier)
    assert store.state(identifier)['attempt']==1
    for i,item in enumerate(data['files']):
        if item['path']=='capture.json':continue
        content=(root/item['path']).read_bytes()
        store.upload_chunk(identifier,i,0,content,hashlib.sha256(content).hexdigest())
    # Upgrade only after the complete inventory passes; hold execution if the
    # runtime is unavailable. The preserved assets must not need another upload.
    store.runtime_ready=lambda:False
    with pytest.raises(ValueError,match='runtime'):store.start(identifier)
    state=store.state(identifier)
    from capture_splat.full_capture_bridge import REVISION
    assert state['pipeline_revision']==REVISION and state['attempt']==2
    assert len(state['uploaded'])==len(data['files'])


def test_model_frame_binding_rejects_mismatched_capture_or_normalized_world(tmp_path):
    from capture_splat.full_capture_bridge import REVISION
    root,data,body=package(tmp_path)
    store=FullCaptureStore(tmp_path/'scans',lambda _:None);state=store.create(body)
    folder=store.folder(state['id']);result=folder/'result';(result/'inference').mkdir(parents=True)
    model=b'synthetic model bytes for receipt validation'
    (result/'inference/gaussians.ply').write_bytes(model)
    capture_sha=hashlib.sha256((folder/'capture/capture.json').read_bytes()).hexdigest()
    report={'ply_sha256':hashlib.sha256(model).hexdigest(),'depth_loss_steps':1,'depth_samples_consumed':1,
            'depth_loss_differentiable':True,'video_training_images':['000001.jpg'],
            'source_manifest_sha256':capture_sha,'world_normalization_applied':False}
    report_path=result/'training-consumption.json';report_path.write_text(json.dumps(report))
    receipt={'pipeline_revision':REVISION,'profile':'rgbd','result_format':'ply','sha256':report['ply_sha256'],
             'bytes':len(model),'training_consumption_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),
             'model_coordinate_frame':'arkit_world','model_units':'meters','source_capture_sha256':capture_sha}
    assert store._verified_result(state['id'],receipt)[0]==model
    receipt['source_capture_sha256']='0'*64
    with pytest.raises(ValueError,match='frame binding'):store._verified_result(state['id'],receipt)
    receipt['source_capture_sha256']=capture_sha;report['world_normalization_applied']=True
    report_path.write_text(json.dumps(report));receipt['training_consumption_sha256']=hashlib.sha256(report_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='frame binding'):store._verified_result(state['id'],receipt)
