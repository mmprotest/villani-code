import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
class State:
    ready=False; jobs=[]; processed=[]
state=State()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs): return
    def do_GET(self):
        if self.path == "/health": code, body = (200 if state.ready else 503), {"ready": state.ready}
        elif self.path == "/jobs": code, body = 200, {"jobs": state.jobs, "processed": state.processed}
        else: code, body = 404, {"error":"not found"}
        self.send_response(code); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(json.dumps(body).encode())
    def do_POST(self):
        if self.path != "/seed": self.send_response(404); self.end_headers(); return
        length=int(self.headers.get("Content-Length","0")); payload=json.loads(self.rfile.read(length).decode() or "{}")
        state.jobs.extend(payload.get("jobs", [])); self.send_response(202); self.end_headers()
def run_api(port=8123): ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
