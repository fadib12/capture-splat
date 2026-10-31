import hashlib
from http.client import HTTPConnection
from http.server import HTTPServer
import json
import threading
import time

import pytest

from capture_splat.pc_bridge import (BridgeStore, handler, image_inventory,
                                     strict_json, wsl_worker)
from tests.test_resplat_adapter import room


@pytest.fixture
def bridge(tmp_path):
    store = BridgeStore(tmp_path / 'scans', lambda _: (_ for _ in ()).throw(RuntimeError('GPU unavailable')))
    server = HTTPServer(('127.0.0.1', 0), handler(store, 'test-local-token'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield store, server.server_address[1], (room(tmp_path / 'capture') / 'capture.json').read_bytes()
    server.shutdown(); server.server_close(); thread.join()


def request(port, path, method='GET', body=None, authorized=True, **headers):
    connection = HTTPConnection('127.0.0.1', port, timeout=15)
    if authorized: headers['Authorization'] = 'Bearer test-local-token'
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    content = response.read()
    status = response.status
    connection.close()
    return status, json.loads(content)


def test_authentication_precedes_manifest_processing(bridge):
    store, port, body = bridge
    assert request(port, '/v1/scans', 'POST', body, authorized=False)[0] == 401
    assert not list(store.root.iterdir())
    assert request(port, '/v1/health', authorized=False)[0] == 401


def test_manifest_idempotency_and_missing_images_block_execution(bridge):
    store, port, body = bridge
    status, state = request(port, '/v1/scans', 'POST', body)
    assert status == 200
    assert state['id'] == hashlib.sha256(body).hexdigest()
    assert request(port, '/v1/scans', 'POST', body)[1]['id'] == state['id']
    assert request(port, f'/v1/scans/{state["id"]}/run', 'POST')[0] == 400
    assert store.state(state['id'])['status'] == 'uploading'


def test_image_hash_binding_and_closed_uploads(bridge):
    store, port, manifest = bridge
    _, state = request(port, '/v1/scans', 'POST', manifest)
    path = f'/v1/scans/{state["id"]}/images/0'
    body = b'fixture bytes'
    digest = hashlib.sha256(body).hexdigest()
    assert request(port, path, 'PUT', body, **{'X-Content-SHA256': '0' * 64})[0] == 400
    assert store.state(state['id'])['uploaded'] == {}
    assert request(port, path, 'PUT', body, **{'X-Content-SHA256': digest})[0] == 200
    assert request(port, path, 'PUT', body, **{'X-Content-SHA256': digest})[0] == 200
    other = b'changed'
    assert request(port, path, 'PUT', other, **{'X-Content-SHA256': hashlib.sha256(other).hexdigest()})[0] == 400
    assert (store.folder(state['id']) / 'capture/rgb/000001.jpg').read_bytes() == body


def test_failed_worker_never_becomes_a_completed_splat(bridge):
    store, port, body = bridge
    _, state = request(port, '/v1/scans', 'POST', body)
    for i in range(8):
        payload = b'test-image'
        assert request(port, f'/v1/scans/{state["id"]}/images/{i}', 'PUT', payload,
                       **{'X-Content-SHA256': hashlib.sha256(payload).hexdigest()})[0] == 200
    assert request(port, f'/v1/scans/{state["id"]}/run', 'POST')[0] == 200
    for _ in range(100):
        if store.state(state['id'])['status'] == 'failed': break
        time.sleep(.01)
    assert store.state(state['id'])['status'] == 'failed'
    assert request(port, f'/v1/scans/{state["id"]}/model')[0] == 409


def test_restart_marks_interrupted_work_failed(tmp_path):
    store = BridgeStore(tmp_path, None)
    identifier = 'a' * 64
    store.folder(identifier).mkdir()
    store.save(identifier, {'id': identifier, 'status': 'processing'})
    recovered = BridgeStore(tmp_path, None)
    assert recovered.state(identifier)['status'] == 'failed'


@pytest.mark.parametrize('path', ['../outside.jpg', 'rgb/../outside.jpg', '/tmp/a.jpg', 'rgb\\a.jpg'])
def test_image_inventory_rejects_paths(bridge, path):
    _, _, body = bridge
    data = json.loads(body)
    data['frames'][0]['rgb'] = path
    with pytest.raises(ValueError): image_inventory(data)


def test_strict_json_rejects_duplicates_and_nonfinite():
    for body in [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}']:
        with pytest.raises(ValueError): strict_json(body)


def test_unready_runtime_preserves_uploaded_scan(tmp_path):
    store = BridgeStore(tmp_path / 'scans', None, runtime_ready=lambda: False)
    manifest = (room(tmp_path / 'capture') / 'capture.json').read_bytes()
    state = store.create(manifest)
    state['uploaded'] = {str(i): {'bytes': 1, 'sha256': 'a' * 64} for i in range(8)}
    store.save(state['id'], state)
    with pytest.raises(ValueError, match='still being installed'):
        store.start(state['id'])
    assert store.state(state['id'])['status'] == 'uploading'
    assert not store.active.locked()


def test_explicit_resubmission_preserves_failed_result_and_uploaded_images(tmp_path):
    store = BridgeStore(tmp_path / 'scans', None)
    manifest = (room(tmp_path / 'capture') / 'capture.json').read_bytes()
    state = store.create(manifest)
    uploaded_image = store.folder(state['id']) / 'capture' / state['images'][0]
    uploaded_image.write_bytes(b'verified upload')
    state.update(status='failed', uploaded={'0': {'bytes': uploaded_image.stat().st_size,
                 'sha256': hashlib.sha256(uploaded_image.read_bytes()).hexdigest()}})
    store.save(state['id'], state)
    result = store.folder(state['id']) / 'result'
    result.mkdir()
    (result / 'diagnostic.txt').write_text('retain')
    retried = store.create(manifest)
    assert retried['status'] == 'uploading' and retried['attempt'] == 2
    assert retried['uploaded'] == state['uploaded']
    assert not result.exists()
    assert (store.folder(state['id']) / 'failed-result-1/diagnostic.txt').read_text() == 'retain'



def populate_uploads(store, state):
    for i, relative in enumerate(state['images']):
        payload = f'uploaded fixture {i}'.encode()
        (store.folder(state['id']) / 'capture' / relative).write_bytes(payload)
        state['uploaded'][str(i)] = {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    store.save(state['id'], state)
    return state


def create_uploaded_scan(tmp_path, worker=None, **kwargs):
    store = BridgeStore(tmp_path / 'scans', worker, **kwargs)
    manifest = (room(tmp_path / 'capture') / 'capture.json').read_bytes()
    state = populate_uploads(store, store.create(manifest))
    return store, manifest, state


def result_worker(*, profile='small', revision=None, result_format='ply'):
    def run(folder):
        # A full SH3 Gaussian fixture; the bridge transports exact bound bytes.
        names = (['x', 'y', 'z', 'nx', 'ny', 'nz'] + [f'f_dc_{i}' for i in range(3)]
                 + [f'f_rest_{i}' for i in range(45)] + ['opacity']
                 + [f'scale_{i}' for i in range(3)] + [f'rot_{i}' for i in range(4)])
        header = 'ply\nformat ascii 1.0\nelement vertex 1\n'
        header += ''.join(f'property float {name}\n' for name in names) + 'end_header\n'
        values = dict.fromkeys(names, 0)
        values.update(z=1, scale_0=-3, scale_1=-3, scale_2=-3, rot_0=1)
        ply = (header + ' '.join(str(values[name]) for name in names) + '\n').encode()
        result = folder / 'result'
        (result / 'inference').mkdir(parents=True)
        (result / 'inference/gaussians.ply').write_bytes(ply)
        splat = bytes(32)
        (result / 'model.splat').write_bytes(splat)
        report = {'world_covariance_export_verified': True, 'ply_covariance_verified': True,
                  'ply_sha256': hashlib.sha256(ply).hexdigest()}
        report_bytes = json.dumps(report).encode()
        (result / 'inference/covariance-export.json').write_bytes(report_bytes)
        payload = ply if result_format == 'ply' else splat
        return {'pipeline_revision': revision or f'resplat-worldcov-v2-{profile}', 'profile': profile,
                'result_format': result_format,
                'result_path': 'inference/gaussians.ply' if result_format == 'ply' else 'model.splat',
                'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload),
                'source_sha256': hashlib.sha256(ply).hexdigest(),
                'covariance_export_report': 'inference/covariance-export.json',
                'covariance_export_report_sha256': hashlib.sha256(report_bytes).hexdigest(),
                'viewer_camera': {'position': [0, 0, 0]}}
    return run


def finish_run(store, identifier):
    store.start(identifier)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = store.state(identifier)
        if state['status'] != 'processing':
            return state
        time.sleep(.01)
    pytest.fail('Bounded bridge fixture worker did not finish.')


def download_model(store, identifier):
    server = HTTPServer(('127.0.0.1', 0), handler(store, 'test-local-token'))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection('127.0.0.1', server.server_address[1], timeout=5)
        connection.request('GET', f'/v1/scans/{identifier}/model',
                           headers={'Authorization': 'Bearer test-local-token'})
        response = connection.getresponse()
        content = response.read()
        answer = response.status, dict(response.getheaders()), content
        connection.close()
        return answer
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_legacy_completed_cache_upgrades_only_on_explicit_resubmission(tmp_path):
    store, manifest, state = create_uploaded_scan(tmp_path)
    state.update(status='completed', result_sha256='a' * 64, result_bytes=32)
    for key in ('pipeline_revision', 'profile', 'revision_attempt', 'attempts_by_revision'):
        state.pop(key, None)
    store.save(state['id'], state)
    folder = store.folder(state['id'])
    (folder / 'result').mkdir()
    (folder / 'result/model.splat').write_bytes(b'old model')
    (folder / 'worker.log').write_text('old execution evidence')
    restarted = BridgeStore(store.root, None, profile='detail')
    assert restarted.state(state['id']) == state
    assert download_model(restarted, state['id'])[0] == 409
    assert restarted.state(state['id']) == state
    upgraded = restarted.create(manifest)
    assert upgraded['status'] == 'uploading' and upgraded['attempt'] == 2
    assert upgraded['pipeline_revision'] == 'resplat-worldcov-v2-detail'
    assert upgraded['revision_attempt'] == 1
    assert upgraded['uploaded'] == state['uploaded']
    assert 'result_sha256' not in upgraded
    assert (folder / 'superseded-result-1/model.splat').read_bytes() == b'old model'
    assert (folder / 'superseded-worker.log-1').read_text() == 'old execution evidence'
    for index, binding in upgraded['uploaded'].items():
        assert hashlib.sha256((folder / 'capture' / upgraded['images'][int(index)]).read_bytes()).hexdigest() == binding['sha256']


@pytest.mark.parametrize('result_format', ['ply', 'splat'])
def test_corrected_result_is_bound_downloadable_and_cached(tmp_path, result_format):
    store, manifest, state = create_uploaded_scan(tmp_path, result_worker(result_format=result_format))
    completed = finish_run(store, state['id'])
    assert completed['status'] == 'completed', (store.folder(state['id']) / 'worker.log').read_text()
    assert completed['result_format'] == result_format
    assert completed['decision'] == 'hold'
    status, headers, body = download_model(store, state['id'])
    assert status == 200 and headers['X-Model-Format'] == result_format
    assert len(body) == completed['result_bytes']
    assert hashlib.sha256(body).hexdigest() == completed['result_sha256']
    assert store.create(manifest) == completed
    assert store.start(state['id']) == completed
    assert not (store.folder(state['id']) / 'superseded-result-1').exists()


def test_failed_retries_are_bounded_per_revision(tmp_path):
    store, manifest, state = create_uploaded_scan(tmp_path)
    for expected in (2, 3):
        state['status'] = 'failed'
        store.save(state['id'], state)
        state = store.create(manifest)
        assert state['attempt'] == expected and state['revision_attempt'] == expected
    state['status'] = 'failed'
    store.save(state['id'], state)
    with pytest.raises(ValueError, match='Retry limit'):
        store.create(manifest)
    assert store.state(state['id'])['attempt'] == 3


def test_switching_profile_preserves_each_revision_retry_limit(tmp_path):
    store, manifest, state = create_uploaded_scan(tmp_path)
    for _ in range(2):
        state['status'] = 'failed'
        store.save(state['id'], state)
        state = store.create(manifest)
    state['status'] = 'failed'
    store.save(state['id'], state)
    detail = BridgeStore(store.root, None, profile='detail')
    changed = detail.create(manifest)
    assert changed['attempt'] == 4 and changed['revision_attempt'] == 1
    assert changed['attempts_by_revision']['resplat-worldcov-v2-small'] == 3
    changed['status'] = 'failed'
    detail.save(state['id'], changed)
    with pytest.raises(ValueError, match='Retry limit'):
        store.create(manifest)


def test_total_retry_limit_cannot_be_reset_by_a_new_revision(tmp_path):
    store, manifest, state = create_uploaded_scan(tmp_path)
    state.update(status='failed', attempt=12,
                 attempts_by_revision={f'resplat-previous-v{i}': 3 for i in range(4)})
    store.save(state['id'], state)
    with pytest.raises(ValueError, match='Retry limit'):
        BridgeStore(store.root, None, profile='detail').create(manifest)
    assert store.state(state['id'])['attempt'] == 12


def test_resubmission_rejects_tampered_uploaded_image_before_archiving(tmp_path):
    store, manifest, state = create_uploaded_scan(tmp_path)
    state['status'] = 'failed'
    store.save(state['id'], state)
    folder = store.folder(state['id'])
    (folder / 'result').mkdir()
    (folder / 'result/retain.txt').write_text('retain')
    (folder / 'capture' / state['images'][0]).write_bytes(b'edited source')
    with pytest.raises(ValueError, match='evidence changed'):
        store.create(manifest)
    assert (folder / 'result/retain.txt').read_text() == 'retain'
    assert store.state(state['id'])['attempt'] == 1


@pytest.mark.parametrize('tamper', ['ply', 'report', 'report_binding', 'size', 'path'])
def test_model_download_revalidates_bytes_and_bindings_before_headers(tmp_path, tamper):
    store, _, state = create_uploaded_scan(tmp_path, result_worker())
    completed = finish_run(store, state['id'])
    assert completed['status'] == 'completed', (store.folder(state['id']) / 'worker.log').read_text()
    folder = store.folder(state['id'])
    if tamper == 'ply':
        model = folder / 'result/inference/gaussians.ply'
        model.write_bytes(model.read_bytes().replace(b'rot_0', b'rot_X', 1))
    elif tamper == 'report':
        report = folder / 'result/inference/covariance-export.json'
        report.write_text(report.read_text().replace('true', 'false', 1))
    elif tamper == 'report_binding':
        completed['covariance_export_report_sha256'] = 'a' * 64
    elif tamper == 'size':
        completed['result_bytes'] += 1
    else:
        completed['result_path'] = '../outside.ply'
    store.save(state['id'], completed)
    status, headers, body = download_model(store, state['id'])
    assert status == 404
    assert headers['Content-Type'] == 'application/json'
    assert 'X-Content-SHA256' not in headers
    assert json.loads(body) == {'error': 'Scan unavailable.'}


@pytest.mark.parametrize('profile,revision', [('small', 'resplat-worldcov-v1-small'),
                                            ('detail', 'resplat-worldcov-v2-detail')])
def test_mismatched_worker_revision_or_profile_never_completes(tmp_path, profile, revision):
    store, _, state = create_uploaded_scan(tmp_path, result_worker(profile=profile, revision=revision))
    failed = finish_run(store, state['id'])
    assert failed['status'] == 'failed'
    assert 'result_sha256' not in failed
    assert download_model(store, state['id'])[0] == 409


def test_wsl_worker_passes_configured_profile_as_an_argv_item(tmp_path, monkeypatch):
    folder = tmp_path / 'scans' / ('a' * 64)
    folder.mkdir(parents=True)
    calls = []
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        (folder / 'result').mkdir()
        (folder / 'result/result.json').write_text('{"fixture": true}')
    monkeypatch.setattr('capture_splat.pc_bridge.subprocess.run', fake_run)
    assert wsl_worker(tmp_path, 'detail')(folder) == {'fixture': True}
    assert calls[0][0][-2:] == ['--profile', 'detail']
    assert calls[0][1]['check'] is True and calls[0][1]['timeout'] == 2100



def test_compact_model_also_revalidates_its_bound_source_ply(tmp_path):
    store, _, state = create_uploaded_scan(tmp_path, result_worker(result_format='splat'))
    completed = finish_run(store, state['id'])
    assert completed['status'] == 'completed'
    model = store.folder(state['id']) / 'result/inference/gaussians.ply'
    model.write_bytes(model.read_bytes().replace(b'rot_0', b'rot_X', 1))
    assert download_model(store, state['id'])[0] == 404


def test_run_rejects_modified_manifest_and_releases_gpu_slot(tmp_path):
    store, _, state = create_uploaded_scan(tmp_path, result_worker())
    manifest = store.folder(state['id']) / 'capture/capture.json'
    manifest.write_bytes(manifest.read_bytes() + b' ')
    with pytest.raises(ValueError, match='manifest evidence changed'):
        store.start(state['id'])
    assert not store.active.locked()
    assert store.state(state['id'])['status'] == 'uploading'
