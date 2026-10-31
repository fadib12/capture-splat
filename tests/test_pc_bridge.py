import hashlib
from http.client import HTTPConnection
from http.server import HTTPServer
import json
import threading
import time

import pytest

from capture_splat.pc_bridge import BridgeStore, handler, image_inventory, strict_json
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
