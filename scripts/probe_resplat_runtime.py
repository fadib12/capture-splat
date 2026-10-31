"""Exercise actual GPU kernels before opening the local reconstruction gate."""
import json
from pathlib import Path
import torch
import gsplat
import pointops

if __name__ == '__main__':
    assert torch.cuda.is_available()
    xyz=torch.tensor([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]], device='cuda')
    offsets=torch.tensor([4], device='cuda', dtype=torch.int32)
    indices,distances=pointops.knn_query(2,xyz,offsets)
    assert indices.shape==(4,2) and torch.isfinite(distances).all()
    colors,alphas,_=gsplat.rasterization(
        means=torch.tensor([[0.,0.,3.]],device='cuda'),
        quats=torch.tensor([[1.,0.,0.,0.]],device='cuda'),
        scales=torch.tensor([[.2,.2,.2]],device='cuda'),
        opacities=torch.tensor([.8],device='cuda'),
        colors=torch.tensor([[1.,0.,0.]],device='cuda'),
        viewmats=torch.eye(4,device='cuda')[None],
        Ks=torch.tensor([[[20.,0.,8.],[0.,20.,8.],[0.,0.,1.]]],device='cuda'),
        width=16,height=16,packed=False)
    torch.cuda.synchronize()
    assert torch.isfinite(colors).all() and alphas.max().item()>0
    receipt={'torch':torch.__version__,'gsplat':gsplat.__version__,
             'gpu':torch.cuda.get_device_name(0),'gpu_memory_bytes':torch.cuda.get_device_properties(0).total_memory,
             'knn_gpu_passed':True,'gaussian_gpu_render_passed':True,'model_quality_validated':False}
    out=Path(__file__).resolve().parents[1]/'runs/pc-bridge/runtime-ready.json'
    out.write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
