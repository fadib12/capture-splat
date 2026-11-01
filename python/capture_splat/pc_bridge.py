"""Single-operator experimental TLS bridge for finalized RGB/camera submissions.

Local job state describes execution only, never quality or World Studio promotion.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
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


MAX_MODEL_BYTES = 64 * 1024**2
MAX_IMAGE_BYTES = 25 * 1024**2
MAX_CAPTURE_BYTES = 512 * 1024**2
MAX_REVISION_ATTEMPTS = 3
MAX_TOTAL_ATTEMPTS = 12
MODEL_PATHS = {'ply': 'inference/gaussians.ply', 'splat': 'model.splat'}
COVARIANCE_REPORT_PATH = 'inference/covariance-export.json'


def pipeline_revision(profile: str) -> str:
    if profile not in {'small', 'detail'}:
        raise ValueError('Unsupported ReSplat profile.')
    return f'resplat-worldcov-v2-{profile}'


def _bounded_file(path: Path, parent: Path, maximum: int) -> bytes:
    # Artifact locations are fixed by the bridge, never supplied as free paths.
    # Reject local aliases before opening and hold bounded immutable response bytes.
    relative = path.relative_to(parent)
    current = parent
    for part in relative.parts:
        current /= part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('Aliased bridge artifact path.')
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('Invalid bridge artifact path.')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= maximum:
            raise ValueError('Artifact is empty or exceeds its byte limit.')
        body = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (len(body) != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError('Artifact changed during verification.')
    return body


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _bounded_digest(path: Path, parent: Path, maximum: int) -> str:
    # The larger source PLY is evidence for compact fallback, never a response.
    # Hash it incrementally rather than retaining up to 512 MiB of source bytes.
    relative = path.relative_to(parent)
    current = parent
    for part in relative.parts:
        current /= part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('Aliased bridge artifact path.')
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('Invalid bridge artifact path.')
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= maximum:
            raise ValueError('Artifact is empty or exceeds its byte limit.')
        while chunk := stream.read(min(1024**2, maximum - total + 1)):
            total += len(chunk)
            if total > maximum:
                raise ValueError('Artifact exceeds its byte limit.')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if (total != before.st_size
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError('Artifact changed during verification.')
    return digest.hexdigest()


def _positive_int(value, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('Invalid bounded attempt or byte count.')
    return value


class BridgeStore:
    def __init__(self, root: Path, worker, runtime_ready=None, *, profile='small', revision=None):
        self.profile = profile
        self.pipeline_revision = revision or pipeline_revision(profile)
        pipeline_revision(profile)  # Validate even when the revision is overridden.
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}', self.pipeline_revision):
            raise ValueError('Invalid pipeline revision.')
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.worker = worker
        self.runtime_ready = runtime_ready or (lambda: True)
        self.active = threading.Lock()
        # Windows cannot replace state.json while another thread has it open.
        self.state_io = threading.RLock()
        # Restart recovery does not invalidate completed results or start work.
        # An explicit POST of the same manifest upgrades a completed legacy cache.
        for path in islice(self.root.glob('*/state.json'), 9):
            state = strict_json(path.read_bytes())
            if state['status'] == 'processing':
                state.update(status='failed', message='PC service restarted during reconstruction. Submit a new run.')
                self.save(path.parent.name, state)

    def folder(self, identifier: str) -> Path:
        if not re.fullmatch('[0-9a-f]{64}', identifier):
            raise ValueError('Invalid scan ID.')
        folder = self.root / identifier
        if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
            raise ValueError('Aliased scan directory.')
        return folder

    def state(self, identifier: str) -> dict:
        with self.state_io:
            return strict_json(_bounded_file(self.folder(identifier) / 'state.json', self.root, 4*1024**2))

    def save(self, identifier: str, state: dict):
        with self.state_io:
            path = self.folder(identifier) / 'state.json'
            temporary = path.with_suffix('.pending')
            if temporary.is_symlink() or path.is_symlink():
                raise ValueError('Aliased scan state.')
            temporary.write_text(json.dumps(state, allow_nan=False))
            temporary.replace(path)

    def _verified_uploads(self, identifier: str, state: dict):
        manifest = _bounded_file(self.folder(identifier) / 'capture/capture.json', self.root, 16 * 1024**2)
        if _digest(manifest) != identifier or image_inventory(strict_json(manifest)) != state['images']:
            raise ValueError('Capture manifest evidence changed.')
        uploaded = state['uploaded']
        if not isinstance(uploaded, dict) or len(uploaded) > len(state['images']):
            raise ValueError('Invalid uploaded image inventory.')
        total = 0
        for key, binding in uploaded.items():
            if not re.fullmatch(r'0|[1-9][0-9]{0,2}', key) or int(key) >= len(state['images']):
                raise ValueError('Invalid uploaded image index.')
            size = _positive_int(binding['bytes'], MAX_IMAGE_BYTES)
            total += size
            if total > MAX_CAPTURE_BYTES:
                raise ValueError('Capture exceeds bridge upload limit.')
            image = self.folder(identifier) / 'capture' / state['images'][int(key)]
            body = _bounded_file(image, self.root, MAX_IMAGE_BYTES)
            if len(body) != size or _digest(body) != binding['sha256']:
                raise ValueError('Uploaded image evidence changed.')

    def _resubmit(self, identifier: str, state: dict) -> dict:
        attempt = _positive_int(state.get('attempt', 1), MAX_TOTAL_ATTEMPTS)
        previous_revision = state.get('pipeline_revision', 'legacy')
        attempts = dict(state.get('attempts_by_revision', {previous_revision: attempt}))
        if len(attempts) > MAX_TOTAL_ATTEMPTS:
            raise ValueError('Invalid attempt history.')
        for name, count in attempts.items():
            if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}', name):
                raise ValueError('Invalid attempt revision.')
            _positive_int(count, MAX_TOTAL_ATTEMPTS)
        if sum(attempts.values()) != attempt:
            raise ValueError('Inconsistent attempt history.')
        revision_attempt = attempts.get(self.pipeline_revision, 0)
        if attempt >= MAX_TOTAL_ATTEMPTS or revision_attempt >= MAX_REVISION_ATTEMPTS:
            raise ValueError('Retry limit reached; inspect the retained attempts on the PC.')
        self._verified_uploads(identifier, state)
        folder = self.folder(identifier)
        label = 'failed' if state['status'] == 'failed' else 'superseded'
        archives = []
        for name in ['result', 'worker.log']:
            source = folder / name
            archive = folder / f'{label}-{name}-{attempt}'
            if source.exists() or source.is_symlink():
                if (source.is_symlink() or getattr(source, 'is_junction', lambda: False)()
                        or source.resolve().parent != folder or archive.exists() or archive.is_symlink()):
                    raise ValueError('Unexpected retry archive path.')
                archives.append((source, archive))
        # Every archive path is checked before the first reversible rename.
        for source, archive in archives:
            source.rename(archive)
        attempts[self.pipeline_revision] = revision_attempt + 1
        for name in ['result_sha256', 'result_bytes', 'result_format', 'result_path',
                     'viewer_camera', 'covariance_export_report_sha256', 'source_ply_sha256',
                     'model_coordinate_frame', 'model_units', 'source_capture_sha256']:
            state.pop(name, None)
        state.update(status='uploading', attempt=attempt + 1,
                     pipeline_revision=self.pipeline_revision, profile=self.profile,
                     revision_attempt=revision_attempt + 1, attempts_by_revision=attempts,
                     message='Previous attempt retained. Reusing verified uploaded images.')
        self.save(identifier, state)
        return state

    def create(self, body: bytes) -> dict:
        paths = image_inventory(strict_json(body))
        identifier = _digest(body)
        folder = self.folder(identifier)
        if folder.exists():
            state = self.state(identifier)
            if state['images'] != paths:
                raise ValueError('Stored image inventory changed.')
            if state['status'] == 'failed' or (state['status'] in {'completed', 'uploading'}
                    and state.get('pipeline_revision') != self.pipeline_revision):
                return self._resubmit(identifier, state)
            return state
        if sum(1 for _ in islice(self.root.iterdir(), 8)) >= 8:
            raise ValueError('PC scan limit reached. Archive old bridge scans on the PC first.')
        (folder / 'capture/rgb').mkdir(parents=True)
        (folder / 'capture/capture.json').write_bytes(body)
        state = {'id': identifier, 'status': 'uploading', 'images': paths, 'uploaded': {},
                 'message': 'Waiting for scan images.', 'decision': 'hold', 'attempt': 1,
                 'pipeline_revision': self.pipeline_revision, 'profile': self.profile,
                 'revision_attempt': 1, 'attempts_by_revision': {self.pipeline_revision: 1}}
        self.save(identifier, state)
        return state

    def start(self, identifier: str) -> dict:
        state = self.state(identifier)
        if state.get('pipeline_revision') != self.pipeline_revision:
            raise ValueError('Submit this manifest again to select the current pipeline.')
        if state['status'] in {'processing', 'completed'}:
            return state
        if state['status'] != 'uploading' or len(state['uploaded']) != len(state['images']):
            raise ValueError('All images must finish uploading before reconstruction.')
        if not self.runtime_ready():
            raise ValueError('Reconstruction runtime is still being installed.')
        if not self.active.acquire(blocking=False):
            raise ValueError('PC is reconstructing another scan. Try again after it finishes.')
        try:
            self._verified_uploads(identifier, state)
            state.update(status='processing', message='Reconstructing the full RGB-D scan.' if self.profile == 'rgbd' else 'ReSplat is reconstructing your scan.')
            self.save(identifier, state)
            threading.Thread(target=self._run, args=(identifier,), daemon=True).start()
        except Exception:
            self.active.release()
            raise
        return state

    def _verified_result(self, identifier: str, receipt: dict) -> tuple[bytes, str, str]:
        if (not isinstance(receipt, dict) or receipt.get('pipeline_revision') != self.pipeline_revision
                or receipt.get('profile') != self.profile):
            raise ValueError('Worker pipeline revision/profile mismatch.')
        ensure_finite(receipt)
        format_name = receipt.get('result_format', 'splat')
        relative = MODEL_PATHS.get(format_name)
        if relative is None or receipt.get('result_path', relative) != relative:
            raise ValueError('Unsupported model artifact path or format.')
        folder = self.folder(identifier)
        model = _bounded_file(folder / 'result' / relative, self.root, MAX_MODEL_BYTES)
        if (_digest(model) != receipt['sha256']
                or len(model) != _positive_int(receipt['bytes'], MAX_MODEL_BYTES)):
            raise ValueError('Model checksum or size mismatch.')
        if receipt.get('covariance_export_report') != COVARIANCE_REPORT_PATH:
            raise ValueError('Unexpected covariance report path.')
        report_body = _bounded_file(folder / 'result' / COVARIANCE_REPORT_PATH, self.root, 1024**2)
        report_digest = _digest(report_body)
        if report_digest != receipt['covariance_export_report_sha256']:
            raise ValueError('Covariance report checksum mismatch.')
        report = strict_json(report_body)
        source_digest = receipt['sha256'] if format_name == 'ply' else receipt['source_sha256']
        if (report.get('world_covariance_export_verified') is not True
                or report.get('ply_covariance_verified') is not True
                or report.get('ply_sha256') != source_digest):
            raise ValueError('Covariance export does not bind the PLY.')
        if format_name != 'ply':
            source_hash = _bounded_digest(folder / 'result' / MODEL_PATHS['ply'], self.root, 512 * 1024**2)
            if source_hash != source_digest:
                raise ValueError('Source PLY checksum mismatch.')
        return model, format_name, report_digest

    def model_bytes(self, identifier: str, state: dict) -> bytes:
        # Download checks happen before sending HTTP headers. The exact verified
        # immutable bytes are the response body, avoiding a hash/reopen race.
        receipt = {'pipeline_revision': state['pipeline_revision'], 'profile': state['profile'],
                   'result_format': state['result_format'], 'result_path': state['result_path'],
                   'sha256': state['result_sha256'], 'bytes': state['result_bytes'],
                   'covariance_export_report': COVARIANCE_REPORT_PATH,
                   'covariance_export_report_sha256': state['covariance_export_report_sha256'],
                   'source_sha256': state.get('source_ply_sha256')}
        return self._verified_result(identifier, receipt)[0]

    def _run(self, identifier):
        try:
            receipt = self.worker(self.folder(identifier))
            model, format_name, report_digest = self._verified_result(identifier, receipt)
            state = self.state(identifier)
            if state.get('pipeline_revision') != self.pipeline_revision:
                raise ValueError('Scan pipeline changed during reconstruction.')
            state.update(status='completed', message='Splat ready for viewing; quality not verified.',
                         result_sha256=_digest(model), result_bytes=len(model),
                         result_format=format_name, result_path=MODEL_PATHS[format_name],
                         covariance_export_report_sha256=report_digest,
                         source_ply_sha256=receipt['sha256'] if format_name == 'ply' else receipt['source_sha256'],
                         viewer_camera=receipt['viewer_camera'])
            self.save(identifier, state)
        except Exception as exc:
            # Keep private diagnostic detail on the PC; HTTP state stays generic.
            try:
                with (self.folder(identifier) / 'worker.log').open('a') as log:
                    log.write(f'\nBridge result validation failed: {type(exc).__name__}: {exc}\n')
            except OSError:
                pass
            state = self.state(identifier)
            state.update(status='failed', message='Reconstruction failed. Check this scan\'s worker log on the PC.')
            self.save(identifier, state)
        finally:
            self.active.release()


def wsl_worker(repo: Path, profile: str = 'small'):
    pipeline_revision(profile)
    def run(folder: Path):
        capture = (folder / 'capture').relative_to(repo).as_posix()
        result = (folder / 'result').relative_to(repo).as_posix()
        command = ['wsl', '-d', 'CaptureSplat', '-u', 'root', '--cd', str(repo), '--',
                   '/opt/capture-splat-resplat/bin/python', 'scripts/run_resplat_worker.py',
                   '--capture', capture, '--out', result, '--profile', profile]
        with (folder / 'worker.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=2100)
        return strict_json(_bounded_file(folder / 'result/result.json', repo, 2 * 1024**2))
    return run


def handler(store: BridgeStore, token: str, full_store=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def scan_store(self):
            if self.path.startswith('/v2/'):
                if full_store is None: raise ValueError('Full capture transfer is unavailable.')
                return full_store
            return store

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
                # Drain only a small, bounded rejected body. Closing a Windows
                # socket with unread POST bytes can reset it before the peer
                # receives the 401. Never parse or retain unauthenticated data.
                declared = self.headers.get('Content-Length', '')
                if declared.isascii() and declared.isdigit() and len(declared) <= 6:
                    length = int(declared)
                    if 0 < length <= 65536:
                        previous_timeout = self.connection.gettimeout()
                        self.connection.settimeout(0.1)
                        try:
                            self.rfile.read(length)
                        except (TimeoutError, OSError):
                            pass
                        finally:
                            self.connection.settimeout(previous_timeout)
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
                if self.path in {'/v1/scans','/v2/scans'}:
                    return self.reply(200, self.scan_store().create(self.body(24 * 1024**2)))
                match = re.fullmatch(r'/v[12]/scans/([0-9a-f]{64})/run', self.path)
                if match:
                    return self.reply(200, self.scan_store().start(match[1]))
                self.reply(404, {'error': 'Unknown endpoint.'})
            except (ValueError, OSError, KeyError, TypeError):
                self.close_connection = True
                self.reply(400, {'error': 'Scan rejected, incomplete, or PC busy.'})

        def do_PUT(self):
            if not self.authorized(): return
            try:
                full = re.fullmatch(r'/v2/scans/([0-9a-f]{64})/files/([0-9]{1,4})/chunks/([0-9]{1,3})',self.path)
                if full:
                    from .full_capture_bridge import CHUNK_BYTES
                    self.scan_store().upload_chunk(full[1],int(full[2]),int(full[3]),self.body(CHUNK_BYTES),self.headers.get('X-Content-SHA256'))
                    return self.reply(200,{'uploaded':True})
                match = re.fullmatch(r'/v1/scans/([0-9a-f]{64})/images/([0-9]{1,3})', self.path)
                if not match: raise ValueError('Invalid image endpoint.')
                identifier, index = match[1], int(match[2])
                state = self.scan_store().state(identifier)
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
                    path = self.scan_store().folder(identifier) / 'capture' / state['images'][index]
                    with path.open('xb') as f: f.write(body)
                    state['uploaded'][str(index)] = {'bytes': len(body), 'sha256': digest}
                    self.scan_store().save(identifier, state)
                self.reply(200, {'uploaded': True})
            except (ValueError, OSError, KeyError, TypeError):
                self.close_connection = True
                self.reply(400, {'error': 'Image upload rejected.'})

        def do_GET(self):
            if not self.authorized(): return
            try:
                if self.path == '/v1/health':
                    return self.reply(200, {'engine': 'resplat', 'scope': 'single_operator_pc_trial',
                                            'profile': store.profile, 'pipeline_revision': self.scan_store().pipeline_revision,
                                            'features': ['full_capture_v2'] if full_store is not None else [],
                                            'full_capture_pipeline_revision': full_store.pipeline_revision if full_store is not None else None})
                match = re.fullmatch(r'/v[12]/scans/([0-9a-f]{64})(/model)?', self.path)
                if not match: return self.reply(404, {'error': 'Unknown endpoint.'})
                state = self.scan_store().state(match[1])
                if not match[2]: return self.reply(200, state)
                if state['status'] != 'completed':
                    return self.reply(409, {'error': 'Model not ready.'})
                if state.get('pipeline_revision') != self.scan_store().pipeline_revision:
                    return self.reply(409, {'error': 'Resubmit the scan to upgrade its reconstruction.'})
                model = self.scan_store().model_bytes(match[1], state)
                # Opt-in, lossless wire compression. Digests and size limits
                # always bind the decoded original model, including all SH bands.
                compressed = self.headers.get('X-CaptureSplat-Transport') == 'gzip-v1'
                if compressed:
                    model = gzip.compress(model, compresslevel=1, mtime=0)
                self.send_response(200)
                if compressed:
                    self.send_header('Content-Encoding', 'gzip')
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(model)))
                self.send_header('X-Content-SHA256', state['result_sha256'])
                self.send_header('X-Model-Format', state['result_format'])
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(model)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except (ValueError, OSError, KeyError, TypeError):
                self.reply(404, {'error': 'Scan unavailable.'})
    return Handler
