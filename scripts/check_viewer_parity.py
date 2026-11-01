"""Compare the exact browser viewer with fixed-camera gsplat reference canvases."""
import argparse
import base64
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
from skimage.metrics import structural_similarity

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--chrome', type=Path, required=True)
    p.add_argument('--views', type=int, default=8)
    a = p.parse_args()
    if not 1 <= a.views <= 8:
        p.error('Use one to eight bound reference views.')
    reference = json.loads((a.reference/'comparison.json').read_text())
    if sha(a.model) != reference['models'][0]['sha256']:
        raise ValueError('Reference canvases belong to another model.')
    a.out.mkdir(parents=True, exist_ok=False)
    browser_temp=a.out/'browser-temp';browser_temp.mkdir()
    for key in ('TEMP','TMP','TMPDIR'):os.environ[key]=str(browser_temp.resolve())
    viewer = ROOT/'apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer'
    for name in ('index.html', 'main.js'):
        shutil.copyfile(viewer/name, a.out/name)
    shutil.copyfile(a.model, a.out/'model.ply')
    html = (a.out/'index.html').read_text(encoding='utf-8')
    html = html.replace('<script src="main.js">', '<script>window.CAPTURE_SPLAT_DIAGNOSTICS=true;window.CAPTURE_SPLAT_MODEL={format:"ply"};</script><script src="main.js">')
    (a.out/'index.html').write_text(html, encoding='utf-8')
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(a.out.resolve())))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    profiles = {'current_radial': (0, .3, True), 'current_depth': (0, .3, False),
                'classic_radial': (.3, 0, True), 'classic_depth': (.3, 0, False)}
    report = {'schema':'capture_splat.viewer_parity.v1', 'model_sha256':sha(a.model),
              'viewer_sha256':sha(viewer/'main.js'), 'reference_sha256':sha(a.reference/'comparison.json'),
              'decision':'hold', 'profiles':{}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(a.chrome), headless=True,
                args=['--enable-unsafe-swiftshader', '--use-angle=swiftshader'])
            page = browser.new_page(viewport={'width':960,'height':720}, device_scale_factor=1)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/', timeout=120000)
            page.wait_for_function('() => window.captureSplatViewer?.getState().ready', timeout=120000)
            for label, (pre, blur, radial) in profiles.items():
                views = []
                for i, item in enumerate(reference['images'][:a.views]):
                    expected_path = a.reference/f'model-0-view-{i}.png'
                    expected = np.asarray(Image.open(expected_path).convert('RGB'))
                    h, w = expected.shape[:2]
                    frame = item['camera']; intr = frame['intrinsics']
                    K = [[intr['fl_x']*w/intr['w'],0,intr['cx']*w/intr['w']],
                         [0,intr['fl_y']*h/intr['h'],intr['cy']*h/intr['h']], [0,0,1]]
                    options = dict(width=w,height=h,K=K,c2w=frame['transform_matrix'],
                                   preBlurAmount=pre,blurAmount=blur,sortRadial=radial)
                    uri = page.evaluate('(options) => captureSplatParity(options)', options)
                    path = a.out/f'{label}-{i}.png'
                    path.write_bytes(base64.b64decode(uri.split(',',1)[1], validate=True))
                    actual = np.asarray(Image.open(path).convert('RGB'))
                    mse = float(np.mean(((actual.astype(float)-expected)/255.)**2))
                    record = {'name':item['name'],'psnr':float(-10*np.log10(max(mse,1e-12))),
                        'ssim':float(structural_similarity(expected,actual,channel_axis=2,data_range=255)),
                        'reference_sha256':sha(expected_path), 'canvas_sha256':sha(path)}
                    views.append(record)
                    print(label, i, round(record['psnr'],3), round(record['ssim'],4), flush=True)
                report['profiles'][label] = {'views':views, 'mean_psnr':float(np.mean([v['psnr'] for v in views])),
                    'worst_psnr':min(v['psnr'] for v in views),'mean_ssim':float(np.mean([v['ssim'] for v in views]))}
            report['browser_errors'] = errors
            browser.close()
            if errors: raise ValueError('Viewer reported JavaScript errors.')
    finally:
        server.shutdown(); server.server_close()
        (a.out/'parity.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')


if __name__ == '__main__': main()
