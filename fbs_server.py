"""
fbs_server.py - MDF Full Body Swap Local Server
Place at: MDF 2026\fbs_server.py

Serves fbs_ui.html AND proxies API calls to the AWS licence server.
Browser talks to localhost only — no cross-origin issues.
"""
import sys, json, threading, webbrowser, time, argparse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request
from urllib.error import URLError

HTML_FILE = Path(__file__).parent / "fbs_ui.html"
PORT = 7860

class Handler(BaseHTTPRequestHandler):
    server_url = "http://16.170.215.170:8000"

    def log_message(self, *a): pass

    def do_GET(self):
        if self.path in ('/', '/index.html', ''):
            try:
                html = HTML_FILE.read_text(encoding='utf-8')
                # Inject config — tell the page to use /api/ proxy endpoints
                config = f'<script>window.MDF_CONFIG={{server_url:"/api",version:"2026.1"}};</script>'
                html = html.replace('</head>', config + '</head>', 1)
                body = html.encode('utf-8')
                self._respond(200, 'text/html', body)
            except FileNotFoundError:
                self._respond(404, 'text/plain', b'fbs_ui.html not found in MDF 2026 folder')
        elif self.path == '/decart-sdk.js':
            sdk_file = Path(__file__).parent.parent / 'mdf-server' / 'decart-sdk.js'
            # Also check same folder as fbs_server.py
            if not sdk_file.exists():
                sdk_file = Path(__file__).parent / 'decart-sdk.js'
            if sdk_file.exists():
                body = sdk_file.read_bytes()
                self._respond(200, 'application/javascript', body)
            else:
                self._respond(404, 'text/plain', b'SDK not built yet')

        elif self.path == '/health':
            self._respond(200, 'application/json', b'{"status":"ok"}')
        else:
            self._respond(404, 'text/plain', b'Not found')

    def do_POST(self):
        if not self.path.startswith('/api/'):
            self._respond(404, 'text/plain', b'Not found')
            return
        real_path = self.path[4:]
        real_url  = self.server_url + real_path
        length    = int(self.headers.get('Content-Length', 0))
        body      = self.rfile.read(length) if length else b''
        try:
            from urllib.request import Request as Req, urlopen as uopen
            from urllib.error import HTTPError as HErr
            req = Req(real_url, data=body,
                      headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with uopen(req, timeout=10) as resp:
                    self._respond(resp.status, 'application/json', resp.read())
            except HErr as e:
                # 4xx/5xx — pass body through so client sees detail message
                self._respond(e.code, 'application/json', e.read())
        except Exception as e:
            self._respond(502, 'application/json',
                          ('{"detail":"' + str(e) + '"}').encode())


    def _respond(self, code, content_type, body):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

def run(server_url=None, port=PORT):
    if server_url:
        Handler.server_url = server_url
    httpd = HTTPServer(('127.0.0.1', port), Handler)
    print(f"[FBS] Running at http://127.0.0.1:{port}")
    print(f"[FBS] Proxying to {Handler.server_url}")
    def open_b(): time.sleep(0.8); webbrowser.open(f"http://127.0.0.1:{port}/")
    threading.Thread(target=open_b, daemon=True).start()
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally: httpd.server_close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument('--server', default='http://16.170.215.170:8000')
    p.add_argument('--port', type=int, default=PORT)
    p.add_argument('--key', default='')
    p.add_argument('--token', default='')
    args = p.parse_args()
    run(args.server, args.port)
