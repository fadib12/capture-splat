"""Compare original and candidate rooms against full-resolution captured images."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from PIL import Image,ImageDraw
import torch
from gsplat import rasterization
from plyfile import PlyData
from skimage.metrics import structural_similarity
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from capture_splat.rgbd_training import sha256
from capture_splat.json_utils import load_json_strict,write_json_strict
from capture_splat.rgbd_alignment import calibration


def load_model(path):
    if not 0<path.stat().st_size<=64*1024**2:raise ValueError('Model exceeds mobile comparison budget.')
    vertices=PlyData.read(path)['vertex'].data
    if not 0<len(vertices)<=300000:raise ValueError('Comparison Gaussian budget exceeded.')
    def fields(names):
        values=np.stack([vertices[n] for n in names],axis=-1).copy()
        if not np.isfinite(values).all():raise ValueError('Non-finite model.')
        return torch.tensor(values,device='cuda',dtype=torch.float32)
    dc=fields([f'f_dc_{i}' for i in range(3)])[:,None,:]
    rest=fields([f'f_rest_{i}' for i in range(45)]).reshape(-1,3,15).transpose(1,2)
    return dict(means=fields(['x','y','z']),quats=torch.nn.functional.normalize(fields([f'rot_{i}' for i in range(4)]),dim=-1),
                scales=fields([f'scale_{i}' for i in range(3)]).exp(),opacities=fields(['opacity'])[:,0].sigmoid(),
                colors=torch.cat((dc,rest),dim=1),sh_degree=3)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--model',type=Path,action='append',required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if not 2<=len(a.model)<=3:p.error('Compare two or three model candidates.')
    a.out.mkdir(parents=True,exist_ok=False);torch.set_num_threads(2)
    index_path=a.run/'dataset/sensor-index.json';index=load_json_strict(index_path)['frames'];validation=sorted(index)[::8]
    selected=[validation[i] for i in np.linspace(0,len(validation)-1,min(8,len(validation)),dtype=int)]
    report={'schema':'capture_splat.room_detail_comparison.v1','decision':'hold','camera_index_sha256':sha256(index_path),
            'evidence_scope':'Identical original ARKit cameras, full-source images; optimization-held-out views, same-capture geometry used in initialization.',
            'camera_axes':'OpenGL c2w @ diag(1,-1,-1,1) exactly once before inversion','images':[],'models':[]}
    sources=[]
    for name in selected:
        path=a.run/'dataset/images'/name
        with Image.open(path) as image:
            if max(image.size)>3840 or image.width*image.height>8294400:raise ValueError('Source image exceeds comparison budget.')
            sources.append(np.asarray(image.convert('RGB')))
        report['images'].append({'name':name,'sha256':sha256(path),'camera':index[name]})
    columns=[[Image.fromarray(s) for s in sources]]
    for mi,path in enumerate(a.model):
        digest=sha256(path);model=load_model(path);views=[];column=[]
        for vi,name in enumerate(selected):
            source=sources[vi];h,w=source.shape[:2];frame=index[name]
            K=torch.tensor(calibration(frame,(w,h)),device='cuda',dtype=torch.float32)[None]
            c2w=np.asarray(frame['transform_matrix'])@np.diag([1,-1,-1,1])
            view=torch.tensor(np.linalg.inv(c2w),device='cuda',dtype=torch.float32)[None]
            with torch.no_grad():
                rgb,alpha,_=rasterization(**model,viewmats=view,Ks=K,width=w,height=h,near_plane=.01,far_plane=100.,packed=True,rasterize_mode='classic')
            prediction=rgb[0].clamp(0,1).cpu().numpy();reference=source.astype(np.float32)/255
            mse=float(np.square(prediction-reference).mean())
            record={'name':name,'width':w,'height':h,'psnr':float(-10*np.log10(max(mse,1e-12))),
                    'ssim':float(structural_similarity(reference,prediction,channel_axis=2,data_range=1.)),
                    'mean_alpha':float(alpha.mean()),'alpha_below_0_5_fraction':float((alpha<.5).float().mean())}
            image=Image.fromarray(np.rint(prediction*255).astype(np.uint8));image.save(a.out/f'model-{mi}-view-{vi}.png')
            column.append(image);views.append(record);print(mi,vi,record,flush=True)
        if sha256(path)!=digest:raise ValueError('Compared model changed during rendering.')
        report['models'].append({'sha256':digest,'gaussians':len(model['means']),'views':views,
                                 'mean_psnr':float(np.mean([v['psnr'] for v in views])),
                                 'worst_psnr':min(v['psnr'] for v in views),
                                 'mean_ssim':float(np.mean([v['ssim'] for v in views]))})
        columns.append(column);del model;torch.cuda.empty_cache()
    for vi in range(len(selected)):
        sheet=Image.new('RGB',(480*len(columns),780),'#181818');draw=ImageDraw.Draw(sheet)
        for ci,column in enumerate(columns):
            label='Captured full-source image' if ci==0 else f'Model {ci-1}'
            draw.text((ci*480+10,8),label,fill='white')
            image=column[vi];sheet.paste(image.resize((480,360)),(ci*480,30))
            # An identical central crop at source pixel resolution, rather than
            # scaling the low-resolution baseline image to pretend it is HD.
            w,h=image.size;crop=image.crop((w//2-240,h//2-180,w//2+240,h//2+180))
            sheet.paste(crop,(ci*480,420));draw.text((ci*480+10,397),'Source-pixel detail crop',fill='white')
        sheet.save(a.out/f'comparison-{vi}.jpg',quality=95)
    if sha256(index_path)!=report['camera_index_sha256']:raise ValueError('Camera index changed.')
    for item in report['images']:
        if sha256(a.run/'dataset/images'/item['name'])!=item['sha256']:raise ValueError('Source image changed.')
    write_json_strict(a.out/'comparison.json',report)

if __name__=='__main__':main()
