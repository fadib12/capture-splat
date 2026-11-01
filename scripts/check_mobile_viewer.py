"""Exercise offline room navigation using browser-generated touch events."""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import threading
from playwright.sync_api import sync_playwright
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from capture_splat.rgbd_training import sha256
from capture_splat.json_utils import load_json_strict,write_json_strict
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True);p.add_argument('--camera',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--chrome',type=Path,required=True)
    a=p.parse_args()
    if not 0<a.model.stat().st_size<=64*1024**2:raise ValueError('Mobile model exceeds budget.')
    camera=load_json_strict(a.camera)
    if 'viewer_camera' in camera:camera=camera['viewer_camera']
    a.out.mkdir(parents=True,exist_ok=False)
    browser_temp=a.out/'browser-temp';browser_temp.mkdir()
    for key in ('TEMP','TMP','TMPDIR'):os.environ[key]=str(browser_temp.resolve())
    viewer=ROOT/'apps/ios/CaptureSplat/CaptureSplat/Resources/SplatViewer'
    for name in ('index.html','main.js'):shutil.copyfile(viewer/name,a.out/name)
    shutil.copyfile(a.model,a.out/'model.ply')
    html=(a.out/'index.html').read_text(encoding='utf-8')
    inject='window.CAPTURE_SPLAT_MODEL={format:"ply"};window.CAPTURE_SPLAT_CAMERA='+json.dumps(camera,allow_nan=False).replace('<','\\u003c')+';'
    html=html.replace('<script src="main.js">','<script>'+inject+'</script><script src="main.js">')
    (a.out/'index.html').write_text(html,encoding='utf-8')
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(a.out.resolve())))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    errors=[];requests=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=str(a.chrome),headless=True,args=['--enable-unsafe-swiftshader','--use-angle=swiftshader'])
            context=browser.new_context(viewport={'width':430,'height':932},device_scale_factor=3,is_mobile=True,has_touch=True)
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:requests.append(r.url))
            url=f'http://127.0.0.1:{server.server_port}/'
            page.goto(url,timeout=120000);page.wait_for_function('() => window.captureSplatViewer?.getState().ready',timeout=120000)
            page.wait_for_timeout(1200)
            state=lambda:page.evaluate('captureSplatViewer.getState()')
            home=state()
            assert home['ready'] and home['format']=='ply' and home['maxSh']==3
            assert math.dist(home['position'],camera['position'])<1e-6
            assert not page.evaluate('typeof window.captureSplatParity !== "undefined"')
            assert math.prod(home['renderSize'])<=8294400 and max(home['renderSize'])<=3840
            page.screenshot(path=str(a.out/'portrait.png'))
            # Lower-resolution preset keeps this software-rendered input check
            # responsive; full Detail buffer and SH3 are checked above.
            page.locator('#quality').click();page.wait_for_timeout(300)
            assert state()['quality']=='smooth' and math.prod(state()['renderSize'])<=2073600
            box=page.locator('#joystick').bounding_box();x=box['x']+box['width']/2;y=box['y']+box['height']/2
            cdp=context.new_cdp_session(page)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y-32,'id':1}]})
            page.wait_for_timeout(700)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y-32,'id':1},{'x':280,'y':350,'id':2}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y-32,'id':1},{'x':345,'y':370,'id':2}]})
            page.wait_for_timeout(450)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]});page.wait_for_timeout(500)
            moved=state()
            assert math.dist(home['position'],moved['position'])>.001
            assert math.dist(home['quaternion'],moved['quaternion'])>.001
            page.wait_for_timeout(400);stopped=state()
            assert math.dist(moved['position'],stopped['position'])<1e-5
            page.locator('#reset').click();page.wait_for_timeout(400)
            reset=state();assert math.dist(home['position'],reset['position'])<1e-7
            assert abs(sum(x*y for x,y in zip(home['quaternion'],reset['quaternion'])))>.999999
            page.locator('#orbit').click();page.wait_for_timeout(200)
            assert state()['mode']=='orbit' and not page.locator('#joystick').is_visible()
            page.locator('#walk').click();page.wait_for_timeout(200)
            assert state()['mode']=='walk' and page.locator('#joystick').is_visible()
            page.set_viewport_size({'width':932,'height':430});page.wait_for_timeout(500)
            assert page.evaluate('document.documentElement.scrollHeight===innerHeight')
            page.screenshot(path=str(a.out/'landscape.png'))
            report={'schema':'capture_splat.mobile_viewer_acceptance.v1','decision':'hold','model_sha256':sha256(a.model),
                    'viewer_sha256':sha256(viewer/'main.js'),'camera_sha256':sha256(a.camera),
                    'home':home,'moved':moved,'stopped':stopped,'reset':reset,'landscape':state(),
                    'errors':errors,'all_requests_loopback':all(u.startswith(url) or u.startswith('blob:'+url) for u in requests),
                    'physical_iphone_acceptance':False}
            assert not errors and report['all_requests_loopback']
            write_json_strict(a.out/'acceptance.json',report)
            print({'touch_move':True,'simultaneous_touch_look':True,'release_stops_motion':True,'reset':True,'offline_assets':True},flush=True)
            browser.close()
    finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
