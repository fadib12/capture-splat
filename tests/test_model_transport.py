"""Exercise real HTTP model transport, including lossless opt-in negotiation."""
import gzip
import hashlib
from http.client import HTTPConnection
from http.server import HTTPServer
from types import SimpleNamespace
import threading
from capture_splat.pc_bridge import handler


def test_lossless_model_transport_preserves_original_digest_and_legacy_bytes():
    data=b'finite SH3 Gaussian model data\0'*4096
    digest=hashlib.sha256(data).hexdigest();identifier='a'*64
    state={'status':'completed','pipeline_revision':'test','result_sha256':digest,'result_format':'ply'}
    store=SimpleNamespace(pipeline_revision='test',state=lambda _:state,model_bytes=lambda *_:data)
    server=HTTPServer(('127.0.0.1',0),handler(store,'test-local-token'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        for compressed in (False,True):
            conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            headers={'Authorization':'Bearer test-local-token'}
            if compressed:headers['X-CaptureSplat-Transport']='gzip-v1'
            conn.request('GET',f'/v1/scans/{identifier}/model',headers=headers)
            response=conn.getresponse();body=response.read()
            assert response.status==200
            assert int(response.getheader('Content-Length'))==len(body)
            assert response.getheader('X-Content-SHA256')==digest
            if compressed:
                assert response.getheader('Content-Encoding')=='gzip' and len(body)<len(data)
                body=gzip.decompress(body)
            else:assert response.getheader('Content-Encoding') is None
            assert body==data
            conn.close()
    finally:server.shutdown();server.server_close();thread.join()
