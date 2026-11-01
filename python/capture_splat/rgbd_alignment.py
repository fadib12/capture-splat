"""Conservative metric RGB-D pose refinement; never edits capture evidence.

This local PnP lane supplements, and does not replace, the frozen COLMAP control.
Only confident measured-depth features anchor the original ARKit world frame.
"""
from pathlib import Path, PurePosixPath
import re
import numpy as np
from .json_utils import load_json_strict, write_json_strict
from .rgbd_training import sha256, sensor_samples
from .colmap_export import ARKIT_TO_OPENCV_CAMERA as FLIP


def calibration(frame, size):
    intr=frame['intrinsics']; w,h=size
    K=np.array([[intr['fl_x']*w/intr['w'],0,intr['cx']*w/intr['w']],
                [0,intr['fl_y']*h/intr['h'],intr['cy']*h/intr['h']],[0,0,1.]],dtype=float)
    if not np.isfinite(K).all() or min(K[0,0],K[1,1])<=0:raise ValueError('Invalid calibration.')
    return K


def project(world, c2w, K):
    pose=np.asarray(c2w,dtype=float)
    if pose.shape!=(4,4) or not np.isfinite(pose).all():raise ValueError('Invalid camera pose.')
    camera=(np.linalg.inv(pose@FLIP)@np.column_stack((world,np.ones(len(world)))).T).T[:,:3]
    xy=camera@K.T
    return xy[:,:2]/xy[:,2:3],camera[:,2]


def accept_correction(original, candidate, world, pixels, K):
    """Gate on independent matches, positive depth, rigid pose and bounded change."""
    original=np.asarray(original);candidate=np.asarray(candidate)
    if candidate.shape!=(4,4) or not np.isfinite(candidate).all():return False,{}
    rotation=candidate[:3,:3]
    if not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-5) or abs(np.linalg.det(rotation)-1)>1e-5:return False,{}
    delta=float(np.linalg.norm(candidate[:3,3]-original[:3,3]))
    angle=float(np.degrees(np.arccos(np.clip((np.trace(rotation.T@original[:3,:3])-1)/2,-1,1))))
    prior,z0=project(world,original,K);refined,z1=project(world,candidate,K)
    before=float(np.median(np.linalg.norm(prior-pixels,axis=1)))
    after=float(np.median(np.linalg.norm(refined-pixels,axis=1)))
    report=dict(heldout_matches=len(world),prior_median_pixels=before,refined_median_pixels=after,
                translation_change_m=delta,rotation_change_degrees=angle)
    okay=(len(world)>=12 and np.isfinite([before,after]).all() and (z0>.05).all() and (z1>.05).all()
          and delta<=.25 and angle<=8 and before>=1 and after<=3 and after<=before*.8)
    return bool(okay),report


def refine(out):
    import cv2
    from PIL import Image
    from .rgbd_seed import _voxel_downsample, _append_colmap_points
    out=Path(out); path=out/'dataset/sensor-index.json'
    index=load_json_strict(path); frames=index['frames']; names=sorted(frames)
    if (out/'camera-alignment.json').exists():raise ValueError('Alignment output already exists.')
    cv2.setNumThreads(2);cv2.setRNGSeed(0)
    detector=cv2.SIFT_create(nfeatures=2200)
    cache={};bindings={};root=out/'prepared/frames'
    for name in names:
        image_path=out/'dataset/images'/name
        image=cv2.imread(str(image_path),cv2.IMREAD_GRAYSCALE)
        if image is None:raise ValueError('Unreadable training image.')
        size=(image.shape[1],image.shape[0]); K=calibration(frames[name],size)
        scale=min(1.,1920/max(size))
        feature_image=cv2.resize(image,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA) if scale<1 else image
        points,descriptors=detector.detectAndCompute(feature_image,None)
        pixels=np.asarray([p.pt for p in points],dtype=np.float64).reshape(-1,2)/scale
        bindings[name]=sha256(image_path)
        cache[name]=(pixels,descriptors,K,size)
    anchors=[n for i,n in enumerate(names) if i%8!=0 and frames[n].get('depth')]
    original_hash=sha256(path); records={}; refined={};matcher=cv2.BFMatcher(cv2.NORM_L2)
    for image_number,name in enumerate(names):
        if image_number%25==0:print(f'Camera alignment: {image_number}/{len(names)}',flush=True)
        frame=frames[name];prior=np.asarray(frame['transform_matrix']); qp,qd,K,size=cache[name]
        if qd is None:continue
        candidates=sorted((n for n in anchors if n!=name),key=lambda n:float(np.linalg.norm(np.asarray(frames[n]['transform_matrix'])[:3,3]-prior[:3,3])))[:6]
        worlds=[];targets=[];used=set()
        for anchor in candidates:
            ap,ad,AK,asize=cache[anchor]
            if ad is None:continue
            af=frames[anchor];depth=np.load(root/af['depth'],allow_pickle=False);conf=np.load(root/af['confidence'],allow_pickle=False)
            h,w=depth.shape
            matches=matcher.knnMatch(qd,ad,k=2)
            for pair in matches:
                if len(pair)!=2 or pair[0].distance>=.7*pair[1].distance:continue
                match=pair[0]
                if match.queryIdx in used:continue
                uv=ap[match.trainIdx];x=int(round(uv[0]*w/asize[0]));y=int(round(uv[1]*h/asize[1]))
                if not 1<=x<w-1 or not 1<=y<h-1:continue
                patch=depth[y-1:y+2,x-1:x+2].astype(float)*float(af.get('depth_scale',1))
                z=float(patch[1,1])
                if not .1<z<8 or not np.isfinite(patch).all() or (conf[y-1:y+2,x-1:x+2]<2).any() or np.ptp(patch)>max(.03,.02*z):continue
                xyz=np.linalg.solve(AK,np.r_[uv,1])*z
                world=(np.asarray(af['transform_matrix'])@FLIP@np.r_[xyz,1])[:3]
                worlds.append(world);targets.append(qp[match.queryIdx]);used.add(match.queryIdx)
        if len(worlds)<60:
            records[name]={'decision':'hold','reason':'insufficient_confident_depth_tracks','matches':len(worlds)};continue
        world=np.asarray(worlds);pixels=np.asarray(targets);held=np.arange(len(world))%4==0
        okay,rvec,tvec,inliers=cv2.solvePnPRansac(world[~held],pixels[~held],K,None,iterationsCount=300,reprojectionError=3,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
        if not okay or inliers is None or len(inliers)<30 or len(inliers)<.6*(~held).sum():
            records[name]={'decision':'hold','reason':'weak_PnP_consensus'};continue
        chosen=inliers[:,0];rvec,tvec=cv2.solvePnPRefineLM(world[~held][chosen],pixels[~held][chosen],K,None,rvec,tvec)
        w2c=np.eye(4);w2c[:3,:3]=cv2.Rodrigues(rvec)[0];w2c[:3,3]=tvec[:,0]
        candidate=np.linalg.inv(w2c)@FLIP
        accepted,record=accept_correction(prior,candidate,world[held],pixels[held],K)
        cells=np.floor(pixels[~held][chosen]/np.array(size)*4).astype(int)
        distributed=len(set(map(tuple,cells)))>=6
        accepted=accepted and distributed
        record.update(decision='promote' if accepted else 'hold',inliers=len(inliers),distributed=distributed)
        records[name]=record
        if accepted:refined[name]=candidate.tolist()
    # Rebuild all initialization from corrected measured rays. Do not mix an
    # unchanged ARKit mesh into locally corrected cameras.
    chunks=[];colors=[];assets={}
    for name in names:
        frame=dict(frames[name]);frame['transform_matrix']=refined.get(name,frame['transform_matrix'])
        K=cache[name][2];size=cache[name][3]
        uv,z,consumed=sensor_samples(root,frame,K,size,maximum=900);assets.update(consumed)
        if not len(z):continue
        xyz=np.linalg.solve(K,np.column_stack((uv,np.ones(len(uv)))).T).T*z[:,None]
        points=(np.asarray(frame['transform_matrix'])@FLIP@np.column_stack((xyz,np.ones(len(xyz)))).T).T[:,:3]
        with Image.open(out/'dataset/images'/name) as image:
            rgb=np.asarray(image.convert('RGB'))
        ix=np.clip(np.rint(uv[:,0]).astype(int),0,size[0]-1);iy=np.clip(np.rint(uv[:,1]).astype(int),0,size[1]-1)
        chunks.append(points);colors.append(rgb[iy,ix])
    if not chunks:raise ValueError('No measured-depth initialization.')
    points,rgb=_voxel_downsample(np.concatenate(chunks),np.concatenate(colors),.025,100000)
    seed=out/'dataset/sparse/0/aligned-points3D.txt'
    seed.write_text('# Corrected measured RGB-D initialization; no mesh seed\n')
    _append_colmap_points(seed,points,rgb)
    report={'schema':'capture_splat.rgbd_alignment.v1','decision':'hold','method':'SIFT metric-depth PnP; held-out match gate; original world retained',
            'index_sha256':original_hash,'image_sha256':bindings,'sensor_assets':assets,'frames':records,'corrected_c2w':refined,
            'seed_path':'sparse/0/aligned-points3D.txt','seed_sha256':sha256(seed),'initial_points':len(points),
            'accepted_cameras':len(refined),'total_cameras':len(names),'heldout_is_independent_capture':False,
            'feature_max_edge':1920,'mesh_seed_used':False,'colmap_control_replaced':False,'authority':{'visual_quality':False,'metric':False,'collision':False}}
    if sha256(path)!=original_hash:raise ValueError('Sensor index changed during alignment.')
    for name,digest in bindings.items():
        if sha256(out/'dataset/images'/name)!=digest:raise ValueError('Image changed during alignment.')
    write_json_strict(out/'camera-alignment.json',report)
    return report


def verify_alignment(out, expected_sha256=None):
    """Revalidate the precise generated camera/seed evidence consumed by training."""
    out=Path(out);path=out/'camera-alignment.json'
    if not 0<path.stat().st_size<=4*1024**2:raise ValueError('Alignment report exceeds limits.')
    digest=sha256(path)
    if expected_sha256 is not None and digest!=expected_sha256:raise ValueError('Consumed camera alignment changed.')
    report=load_json_strict(path)
    if report.get('schema')!='capture_splat.rgbd_alignment.v1':raise ValueError('Unsupported alignment schema.')
    index_path=out/'dataset/sensor-index.json'
    if sha256(index_path)!=report['index_sha256']:raise ValueError('Alignment index binding changed.')
    frames=load_json_strict(index_path)['frames']
    if not 1<=len(frames)<=600 or set(report['image_sha256'])!=set(frames):raise ValueError('Alignment image inventory changed.')
    for name,binding in report['image_sha256'].items():
        if not re.fullmatch(r'[0-9]{6}\.(jpg|png)',name):raise ValueError('Invalid aligned image path.')
        image=out/'dataset/images'/name
        if image.stat().st_size>32*1024**2 or sha256(image)!=binding:raise ValueError('Alignment image binding changed.')
    if len(report['sensor_assets'])>1800:raise ValueError('Alignment sensor inventory exceeds limits.')
    for name,binding in report['sensor_assets'].items():
        relative=PurePosixPath(name)
        if not name or relative.as_posix()!=name or relative.is_absolute() or any(part in ('','.','..') for part in relative.parts) or ':' in name or chr(92) in name:
            raise ValueError('Invalid alignment sensor path.')
        asset=out/'prepared/frames'/relative
        if asset.stat().st_size>4*1024**2 or sha256(asset)!=binding:raise ValueError('Alignment depth binding changed.')
    if report['seed_path']!='sparse/0/aligned-points3D.txt':raise ValueError('Invalid alignment seed path.')
    seed=out/'dataset'/report['seed_path']
    if seed.stat().st_size>32*1024**2 or sha256(seed)!=report['seed_sha256']:raise ValueError('Aligned initialization changed.')
    corrections=report['corrected_c2w']
    if set(corrections)-set(frames) or len(corrections)!=report['accepted_cameras']:raise ValueError('Corrected camera inventory changed.')
    for pose in corrections.values():
        matrix=np.asarray(pose,dtype=float)
        if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3],[0,0,0,1],atol=1e-6):
            raise ValueError('Invalid corrected camera transform.')
        rotation=matrix[:3,:3]
        if not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-4) or abs(np.linalg.det(rotation)-1)>1e-4:
            raise ValueError('Invalid corrected camera rotation.')
    if sha256(path)!=digest or sha256(index_path)!=report['index_sha256']:raise ValueError('Alignment changed during validation.')
    return report
