from http.server import BaseHTTPRequestHandler, HTTPServer
import json, sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            payload = json.dumps({'status': 'ok'}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()
    def log_message(self, format, *args):
        return

def main(port: int = 8765) -> None:
    server = HTTPServer(('127.0.0.1', port), Handler)
    print('READY', flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
