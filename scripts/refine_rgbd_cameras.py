"""Refine prepared RGB-D cameras without modifying original capture files."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from capture_splat.rgbd_alignment import refine
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=refine(a.out);print({'accepted_cameras':r['accepted_cameras'],'total_cameras':r['total_cameras']})
