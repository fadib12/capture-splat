"""Single-operator experimental TLS bridge for finalized RGB/camera submissions.

Local job state describes execution only, never quality or World Studio promotion.
"""
from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from itertools import islice
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading

from .capture_schema import iter_frames
from .json_utils import ensure_finite


def strict_json(body: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('Duplicate JSON key.')
            out[key] = value
        return out
    data = json.loads(body, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Invalid JSON number.')))
    ensure_finite(data)
    return data


def image_inventory(data: dict) -> list[str]:
    if not isinstance(data, dict) or data.get('schema') not in {'capture_splat.v0.1', 'capture_splat.v0.2', 'capture_splat.v0.3'}:
        raise ValueError('Unsupported capture manifest.')
    if not isinstance(data.get('frames'), list) or not 8 <= len(data['frames']) <= 512:
        raise ValueError('Expected 8 to 512 manifest frames.')
    paths = []
    for frame in iter_frames(data, accepted_only=True):
        path = PurePosixPath(frame.image_path)
        if (len(path.parts) != 2 or path.parts[0] != 'rgb' or path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', path.name)):
            raise ValueError('Unsupported RGB image path.')
        paths.append(path.as_posix())
    if len(paths) < 8 or len(set(p.casefold() for p in paths)) != len(paths):
        raise ValueError('Insufficient or duplicate RGB images.')
    return paths


class BridgeStore:
    def __init__(self, root: Path, worker, runtime_ready=None):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker = worker
        self.runtime_ready = runtime_ready or (lambda: True)
        self.active = threading.Lock()
        # An interrupted model run is never presented as completed after restart.
        for path in islice(self.root.glob('*/state.json'), 9):
            state = strict_json(path.read_bytes())
            if state['status'] == 'processing':
                state.update(status='failed', message='PC service restarted during reconstruction. Submit a new run.')
                self.save(path.parent.name, state)

    def folder(self, identifier: str) -> Path:
        if not re.fullmatch('[0-9a-f]{64}', identifier):
            raise ValueError('Invalid scan ID.')
        return self.root / identifier

    def state(self, identifier: str) -> dict:
        return strict_json((self.folder(identifier) / 'state.json').read_bytes())

    def save(self, identifier: str, state: dict):
        path = self.folder(identifier) / 'state.json'
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps(state, allow_nan=False))
        temporary.replace(path)

    def create(self, body: bytes) -> dict:
        paths = image_inventory(strict_json(body))
        identifier = hashlib.sha256(body).hexdigest()
        folder = self.folder(identifier)
        if folder.exists():
            state = self.state(identifier)
            if state['status'] == 'failed':
                attempt = int(state.get('attempt', 1))
                if not 1 <= attempt < 3:
                    raise ValueError('Retry limit reached; inspect the retained attempts on the PC.')
                for name in ['result', 'worker.log']:
                    source = folder / name
                    archive = folder / f'failed-{name}-{attempt}'
                    if source.exists():
                        if source.is_symlink() or source.resolve().parent != folder.resolve() or archive.exists():
                            raise ValueError('Unexpected retry archive path.')
                        source.rename(archive)
                state.update(status='uploading', attempt=attempt + 1,
                             message='Previous attempt retained. Reusing verified uploaded images.')
                self.save(identifier, state)
            return state
        if sum(1 for _ in islice(self.root.iterdir(), 8)) >= 8:
            raise ValueError('PC scan limit reached. Archive old bridge scans on the PC first.')
        (folder / 'capture/rgb').mkdir(parents=True)
        (folder / 'capture/capture.json').write_bytes(body)
        state = {'id': identifier, 'status': 'uploading', 'images': paths, 'uploaded': {},
                 'message': 'Waiting for scan images.', 'decision': 'hold', 'attempt': 1}
        self.save(identifier, state)
        return state

    def start(self, identifier: str) -> dict:
        state = self.state(identifier)
        if state['status'] in {'processing', 'completed'}:
            return state
        if state['status'] != 'uploading' or len(state['uploaded']) != len(state['images']):
            raise ValueError('All images must finish uploading before reconstruction.')
        if not self.runtime_ready():
            raise ValueError('Reconstruction runtime is still being installed.')
        if not self.active.acquire(blocking=False):
            raise ValueError('PC is reconstructing another scan. Try again after it finishes.')
        state.update(status='processing', message='ReSplat is reconstructing your scan.')
        self.save(identifier, state)
        threading.Thread(target=self._run, args=(identifier,), daemon=True).start()
        return state

    def _run(self, identifier):
        try:
            receipt = self.worker(self.folder(identifier))
            model = self.folder(identifier) / 'result/model.splat'
            if not model.is_file() or not 0 < model.stat().st_size <= 64 * 1024**2:
                raise ValueError('Model missing or exceeds the mobile limit.')
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            if digest != receipt['sha256']:
                raise ValueError('Model checksum mismatch.')
            state = self.state(identifier)
            state.update(status='completed', message='Splat ready for viewing; quality not verified.',
                         result_sha256=digest, result_bytes=model.stat().st_size,
                         viewer_camera=receipt['viewer_camera'])
            self.save(identifier, state)
        except Exception:
            state = self.state(identifier)
            state.update(status='failed', message='Reconstruction failed. Check this scan\'s worker log on the PC.')
            self.save(identifier, state)
        finally:
            self.active.release()


def wsl_worker(repo: Path):
    def run(folder: Path):
        capture = (folder / 'capture').relative_to(repo).as_posix()
        result = (folder / 'result').relative_to(repo).as_posix()
        command = ['wsl', '-d', 'CaptureSplat', '-u', 'root', '--cd', str(repo), '--',
                   '/opt/capture-splat-resplat/bin/python', 'scripts/run_resplat_worker.py',
                   '--capture', capture, '--out', result]
        with (folder / 'worker.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=2100)
        return strict_json((folder / 'result/result.json').read_bytes())
    return run


def handler(store: BridgeStore, token: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, *_):
            pass  # Never log Authorization, pairing codes or room metadata.

        def reply(self, status, data):
            body = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.close_connection = True
                self.reply(401, {'error': 'Authentication required.'})
                return False
            return True

        def body(self, maximum):
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Chunked uploads are unsupported.')
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 < length <= maximum:
                raise ValueError('Invalid request size.')
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('Incomplete upload.')
            return data

        def do_POST(self):
            if not self.authorized(): return
            try:
                if self.path == '/v1/scans':
                    return self.reply(200, store.create(self.body(16 * 1024**2)))
                match = re.fullmatch(r'/v1/scans/([0-9a-f]{64})/run', self.path)
                if match:
                    return self.reply(200, store.start(match[1]))
                self.reply(404, {'error': 'Unknown endpoint.'})
            except (ValueError, OSError, KeyError, TypeError):
                self.close_connection = True
                self.reply(400, {'error': 'Scan rejected, incomplete, or PC busy.'})

        def do_PUT(self):
            if not self.authorized(): return
            try:
                match = re.fullmatch(r'/v1/scans/([0-9a-f]{64})/images/([0-9]{1,3})', self.path)
                if not match: raise ValueError('Invalid image endpoint.')
                identifier, index = match[1], int(match[2])
                state = store.state(identifier)
                if state['status'] != 'uploading' or index >= len(state['images']):
                    raise ValueError('Upload is closed or unknown image.')
                body = self.body(25 * 1024**2)
                digest = hashlib.sha256(body).hexdigest()
                if digest != self.headers.get('X-Content-SHA256'):
                    raise ValueError('Image checksum mismatch.')
                if sum(x['bytes'] for x in state['uploaded'].values()) + len(body) > 512 * 1024**2:
                    raise ValueError('Capture exceeds bridge upload limit.')
                old = state['uploaded'].get(str(index))
                if old and old['sha256'] != digest:
                    raise ValueError('Immutable image already uploaded.')
                if not old:
                    path = store.folder(identifier) / 'capture' / state['images'][index]
                    with path.open('xb') as f: f.write(body)
                    state['uploaded'][str(index)] = {'bytes': len(body), 'sha256': digest}
                    store.save(identifier, state)
                self.reply(200, {'uploaded': True})
            except (ValueError, OSError, KeyError, TypeError):
                self.close_connection = True
                self.reply(400, {'error': 'Image upload rejected.'})

        def do_GET(self):
            if not self.authorized(): return
            try:
                if self.path == '/v1/health':
                    return self.reply(200, {'engine': 'resplat', 'scope': 'single_operator_pc_trial'})
                match = re.fullmatch(r'/v1/scans/([0-9a-f]{64})(/model)?', self.path)
                if not match: return self.reply(404, {'error': 'Unknown endpoint.'})
                state = store.state(match[1])
                if not match[2]: return self.reply(200, state)
                if state['status'] != 'completed':
                    return self.reply(409, {'error': 'Model not ready.'})
                model = store.folder(match[1]) / 'result/model.splat'
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(state['result_bytes']))
                self.send_header('X-Content-SHA256', state['result_sha256'])
                self.end_headers()
                with model.open('rb') as f:
                    while chunk := f.read(1024 * 1024): self.wfile.write(chunk)
            except (ValueError, OSError, KeyError):
                self.reply(404, {'error': 'Scan unavailable.'})
    return Handler
