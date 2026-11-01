"""Adapt only data ingestion and empty-depth handling in pinned upstream gsplat."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from capture_splat.rgbd_training import sensor_samples,sha256,TRAINER_COMMIT,metric_depth_loss
from capture_splat.json_utils import load_json_strict
SOURCE_SHA='79319e1cd7404e4d1ba0c425634235c39e6054f0643b904a01feea6179462c05'


def run(out,steps,data_factor=1):
    import subprocess
    upstream_root=ROOT/'external/gsplat-rgbd'
    if subprocess.check_output(['git','-C',str(upstream_root),'rev-parse','HEAD'],text=True).strip()!=TRAINER_COMMIT:
        raise ValueError('Trainer source pin mismatch.')
    path=upstream_root/'examples/simple_trainer.py'
    source=path.read_bytes().replace(b'\r\n',b'\n')
    if hashlib.sha256(source).hexdigest()!=SOURCE_SHA:raise ValueError('Trainer source changed.')
    sys.path.insert(0,str(upstream_root/'examples'))
    spec=importlib.util.spec_from_file_location('capture_upstream_gsplat',path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    import torch
    torch.set_num_threads(2)
    frames_root=out/'prepared/frames'
    source_capture=load_json_strict(frames_root/'metadata/source_capture.json')
    index_path=out/'dataset/sensor-index.json'
    index=load_json_strict(index_path)['frames']
    alignment_path=out/'camera-alignment.json'
    alignment=load_json_strict(alignment_path) if alignment_path.exists() else None
    if alignment:
        if alignment['index_sha256']!=sha256(index_path):raise ValueError('Alignment index binding changed.')
        for name,digest in alignment['image_sha256'].items():
            if sha256(out/'dataset/images'/name)!=digest:raise ValueError('Alignment image binding changed.')
        for name,digest in alignment['sensor_assets'].items():
            if sha256(frames_root/name)!=digest:raise ValueError('Alignment depth binding changed.')
        if sha256(out/'dataset'/alignment['seed_path'])!=alignment['seed_sha256']:
            raise ValueError('Aligned initialization changed.')
    ledger={'depth_loss_steps':0,'depth_samples_consumed':0,'depth_loss_sum':0.,
            'sensor_assets':{},'training_images':{},'video_training_images':[],
            'depth_loss_differentiable':True}
    class SensorParser:
        def __init__(self,data_dir,factor=1,normalize=False,test_every=8):
            from PIL import Image
            from capture_splat.colmap_export import ARKIT_TO_OPENCV_CAMERA
            if normalize or factor not in (1,2):raise ValueError('Unsupported calibrated dataset transform.')
            self.data_dir=data_dir;self.factor=factor;self.normalize=False;self.test_every=test_every
            self.image_names=sorted(index);self.image_paths=[];self.camera_ids=[]
            self.Ks_dict={};self.params_dict={};self.mask_dict={};self.imsize_dict={}
            poses=[]
            cache=out/f'dataset/images_sensor_factor_{factor}';cache.mkdir(exist_ok=True)
            for i,name in enumerate(self.image_names):
                frame=index[name];intr=frame['intrinsics'];source=out/'dataset/images'/name
                with Image.open(source) as image:
                    original_size=image.size
                    size=(original_size[0]//factor,original_size[1]//factor)
                    target=cache/(Path(name).stem+'.png')
                    if not target.exists():image.convert('RGB').resize(size,Image.Resampling.BICUBIC).save(target)
                    with Image.open(target) as cached:
                        if cached.size!=size:raise ValueError('Cached image resolution differs from training configuration.')
                K=np.array([[intr['fl_x']*size[0]/intr['w'],0,intr['cx']*size[0]/intr['w']],
                            [0,intr['fl_y']*size[1]/intr['h'],intr['cy']*size[1]/intr['h']],[0,0,1.]])
                self.image_paths.append(str(target));self.camera_ids.append(i);self.Ks_dict[i]=K
                self.params_dict[i]=np.empty(0);self.mask_dict[i]=None;self.imsize_dict[i]=size
                pose=alignment['corrected_c2w'].get(name,frame['transform_matrix']) if alignment else frame['transform_matrix']
                poses.append(np.asarray(pose)@ARKIT_TO_OPENCV_CAMERA)
            self.camtoworlds=np.stack(poses).astype(np.float32)
            seed=out/'dataset'/alignment['seed_path'] if alignment else out/'dataset/sparse/0/points3D.txt'
            values=np.loadtxt(seed,comments='#',usecols=(1,2,3,4,5,6))
            self.points=values[:,:3].astype(np.float32);self.points_rgb=values[:,3:].astype(np.uint8)
            self.points_err=np.zeros(len(values),np.float32);self.transform=np.eye(4)
            self.point_indices={name:np.empty(0,np.int32) for name in self.image_names}
            centers=self.camtoworlds[:,:3,3]
            self.scene_scale=float(np.max(np.linalg.norm(centers-centers.mean(0),axis=1)))
            if not np.isfinite(self.scene_scale) or self.scene_scale<=0:raise ValueError('Degenerate camera baseline.')
    module.Parser=SensorParser
    original=module.Dataset
    class SensorDataset(original):
        def __init__(self,parser,split='train',patch_size=None,load_depths=False):
            if patch_size is not None or parser.normalize:raise ValueError('Sensor adapter requires uncropped, unnormalized cameras.')
            super().__init__(parser,split=split,patch_size=None,load_depths=False)
            self.sensor_depths=load_depths
        def __getitem__(self,item):
            data=super().__getitem__(item)
            if self.sensor_depths:
                image_index=int(self.indices[item]);name=self.parser.image_names[image_index]
                frame=dict(index[name]);frame.setdefault('depth_scale',float(source_capture.get('depth_scale',1)))
                pixels,depths,assets=sensor_samples(frames_root,frame,data['K'].numpy(),
                                                  (data['image'].shape[1],data['image'].shape[0]))
                data['points']=torch.from_numpy(pixels);data['depths']=torch.from_numpy(depths)
                ledger['sensor_assets'].update(assets)
                ledger['training_images'][name]=sha256(self.parser.image_paths[image_index])
                if frame.get('source_kind')=='continuous_video' and name not in ledger['video_training_images']:
                    ledger['video_training_images'].append(name)
            return data
    module.Dataset=SensorDataset
    def record_depth(depths,loss):
        if not depths.numel(): return
        value=float(loss.detach())
        if not np.isfinite(value):raise ValueError('Non-finite LiDAR loss.')
        ledger['depth_loss_steps']+=1;ledger['depth_samples_consumed']+=int(depths.numel())
        ledger['depth_loss_sum']+=value
        ledger['depth_loss_differentiable'] &= bool(loss.requires_grad and loss.grad_fn is not None)
    tree=ast.parse(source)
    runner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Runner')
    changes={'depth_guard':0,'loaders':0}
    class SensorBoundary(ast.NodeTransformer):
        def visit_If(self,node):
            self.generic_visit(node)
            if (isinstance(node.test,ast.Attribute) and isinstance(node.test.value,ast.Name)
                and node.test.value.id=='cfg' and node.test.attr=='depth_loss'
                and node.body and isinstance(node.body[0],ast.Assign)
                and isinstance(node.body[0].value,ast.Call)
                and isinstance(node.body[0].value.func,ast.Attribute)
                and node.body[0].value.func.attr=='stack'
                and any(isinstance(t,ast.Name) and t.id=='points' for t in node.body[0].targets)):
                node.test=ast.parse('cfg.depth_loss and depths_gt.numel() > 0',mode='eval').body
                filtered=[]
                for statement in node.body:
                    name=statement.targets[0].id if isinstance(statement,ast.Assign) and isinstance(statement.targets[0],ast.Name) else None
                    if name in {'disp','disp_gt'}:continue
                    if name=='depthloss':
                        statement=ast.copy_location(ast.parse('depthloss, used_depths = _capture_metric_depth_loss(depths,depths_gt)').body[0],statement)
                    filtered.append(statement)
                node.body=filtered
                node.body.append(ast.parse('_capture_record_depth(used_depths,depthloss)').body[0])
                changes['depth_guard']+=1
                return [ast.copy_location(ast.parse('depthloss = colors.sum() * 0').body[0],node),node]
            return node
        def visit_Expr(self,node):
            self.generic_visit(node)
            if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='backward':
                return [ast.copy_location(ast.parse('_capture_assert_finite_loss(loss)').body[0],node),node]
            return node
        def visit_Call(self,node):
            self.generic_visit(node)
            if isinstance(node.func,ast.Attribute) and node.func.attr=='DataLoader':
                for kw in node.keywords:
                    if kw.arg=='num_workers':kw.value=ast.Constant(0)
                    if kw.arg=='persistent_workers':kw.value=ast.Constant(False)
                changes['loaders']+=1
            return node
    methods=[n for n in runner.body if isinstance(n,ast.FunctionDef) and n.name in {'train','eval'}]
    namespace=dict(module.__dict__,_capture_record_depth=record_depth,_capture_metric_depth_loss=metric_depth_loss,
                   _capture_assert_finite_loss=lambda loss: None if bool(torch.isfinite(loss)) else (_ for _ in ()).throw(ValueError('Non-finite training loss.')))
    for method in methods:
        changed=SensorBoundary().visit(method)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[changed],type_ignores=[])),str(path),'exec'),namespace)
        setattr(module.Runner,method.name,namespace[method.name])
    if changes['depth_guard']!=1 or changes['loaders']!=2:raise ValueError('Pinned sensor adapter boundary changed.')
    cfg=module.Config(disable_viewer=True,data_dir=str(out/'dataset'),result_dir=str(out/'training'),
                      data_factor=data_factor,normalize_world_space=False,depth_loss=True,depth_lambda=.1,
                      packed=True,save_ply=True,disable_video=True,random_bkgd=False,
                      eval_steps=[30000],save_steps=[30000],ply_steps=[30000],tb_every=100,
                      strategy=module.MCMCStrategy(cap_max=200000,verbose=False),
                      init_opa=.5,init_scale=.5,opacity_reg=.01,scale_reg=.01)
    cfg.adjust_steps(steps/30000)
    cfg.strategy.refine_every=max(200,cfg.strategy.refine_every)
    cfg.sh_degree_interval=max(1,cfg.sh_degree_interval)
    module.main(0,0,1,cfg)
    if not ledger['depth_loss_steps'] or not ledger['depth_loss_differentiable']:
        raise ValueError('No differentiable LiDAR supervision was consumed.')
    ledger.update(trainer_commit=TRAINER_COMMIT,trainer_source_sha256=SOURCE_SHA,
                  adapter='measured_depth_samples_v1',depth_objective='metric_Huber_beta_0.1m; empty/nonfinite rays excluded',depth_units='meters_optical_z',confidence_minimum=1,
                  sparse_colmap_depth_fallback=False,steps=steps,normal_supervision_applied=False,
                  world_normalization_applied=False,data_factor=data_factor,
                  training_cache=f'images_sensor_factor_{data_factor}',
                  camera_alignment_sha256=sha256(alignment_path) if alignment else None,
                  corrected_cameras_consumed=len(alignment['corrected_c2w']) if alignment else 0,
                  rasterization='classic',viewer_profile='classic-depth-v1')
    (out/'training-ledger.json').write_text(json.dumps(ledger,indent=2,allow_nan=False))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--steps',type=int,default=7000)
    p.add_argument('--data-factor',type=int,choices=(1,2),default=1);args=p.parse_args()
    if not 10<=args.steps<=15000:p.error('Steps must be 10..15000.')
    run(args.out.resolve(),args.steps,args.data_factor)
