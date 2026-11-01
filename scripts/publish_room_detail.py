"""Publish a same-input room comparison with the current offline viewer."""
import argparse
import html
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from capture_splat.rgbd_training import sha256
from capture_splat.json_utils import load_json_strict
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--comparison',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--model',type=Path,action='append',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();report=load_json_strict(a.comparison/'comparison.json');receipt=load_json_strict(a.receipt)
    if len(a.model)!=2 or len(report['models'])!=2:p.error('Expected original and candidate models.')
    camera=receipt['viewer_camera'];a.out.mkdir(parents=True,exist_ok=False)
    viewer=ROOT/'apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer'
    labels=['Original reconstruction','Full-resolution candidate']
    links=[];rows=[]
    for i,(path,record) in enumerate(zip(a.model,report['models'])):
        if path.stat().st_size>64*1024**2 or sha256(path)!=record['sha256']:raise ValueError('Model differs from comparison evidence.')
        folder=a.out/str(i);folder.mkdir()
        for name in ('index.html','main.js','LICENSE','THREE-LICENSE'):shutil.copyfile(viewer/name,folder/name)
        page=(folder/'index.html').read_text(encoding='utf-8')
        inject='window.CAPTURE_SPLAT_MODEL={format:"ply"};window.CAPTURE_SPLAT_CAMERA='+json.dumps(camera,allow_nan=False).replace('<','\\u003c')+';'
        page=page.replace('<script src="main.js">','<script>'+inject+'</script><script src="main.js">')
        (folder/'index.html').write_text(page,encoding='utf-8');shutil.copyfile(path,folder/'model.ply')
        links.append(f'<a href="{i}/">Open {html.escape(labels[i])}</a>')
        rows.append(f'<tr><td>{html.escape(labels[i])}</td><td>{record["gaussians"]:,}</td><td>{record["mean_psnr"]:.2f}</td><td>{record["mean_ssim"]:.3f}</td></tr>')
    images=[]
    for i in range(len(report['images'])):
        name=f'comparison-{i}.jpg';shutil.copyfile(a.comparison/name,a.out/name)
        images.append(f'<a href="{name}"><img src="{name}" loading="lazy" alt="Captured source, original reconstruction and candidate at camera {i+1}"></a>')
    shutil.copyfile(a.comparison/'comparison.json',a.out/'comparison.json')
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Room detail comparison</title><style>body{margin:32px auto;max-width:1500px;padding:0 20px;background:#11151b;color:#eef1f6;font:16px/1.6 system-ui}a{color:#69d5ff}.links{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}.links a{padding:12px 20px;background:#263447;border-radius:12px;text-decoration:none}img{width:100%;margin:16px 0}table{border-collapse:collapse}td,th{text-align:left;padding:8px 16px;border-bottom:1px solid #444}</style><h1>Your room: detail comparison</h1><p>Both viewers use the calibrated depth-sorting profile. The candidate uses measured RGB-D camera refinement and full-source images. Review the observed room; unobserved surfaces remain uncertain.</p><div class="links">'''+''.join(links)+'''</div><table><tr><th>Version</th><th>Gaussians</th><th>PSNR</th><th>SSIM</th></tr>'''+''.join(rows)+'''</table><p>Eight optimization-held-out source views at identical original cameras and full source resolution. Same-capture geometry contributes to initialization; these measures do not establish independent property accuracy. Training and camera refinement changed together, so this comparison does not isolate their individual contributions.</p><p>Each image shows the captured source, original model and candidate, followed by the same source-pixel detail crop.</p>'''+''.join(images)+'</html>\n'
    (a.out/'index.html').write_text(page,encoding='utf-8')

if __name__=='__main__':main()
