"""Loopback-only fixture for actual Foundation gzip download acceptance."""
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
import gzip,sys
BODY=b'CaptureSplat lossless SH3 model transfer\n'*16384
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data=gzip.compress(BODY,compresslevel=1,mtime=0)
        self.send_response(200);self.send_header('Content-Encoding','gzip')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass
if __name__=='__main__':
    server=HTTPServer(('127.0.0.1',0),Handler)
    Path(sys.argv[1]).write_text(str(server.server_port))
    server.serve_forever()
