import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional, Tuple

class _Handler(BaseHTTPRequestHandler):
    server_version = "PickServer/1.0"

    def _send_json(self, code: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/pick":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"

        try:
            data = json.loads(body)
            lat = float(data["lat"])
            lon = float(data["lon"])
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid payload"})
            return

        cb: Optional[Callable[[float, float], None]] = getattr(self.server, "on_pick", None)
        if cb:
            cb(lat, lon)

        self._send_json(200, {"ok": True})

    def log_message(self, format, *args):
        return  # quiet

class PickServer:
    def __init__(self, on_pick: Callable[[float, float], None], host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port
        self._on_pick = on_pick
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def address(self) -> Tuple[str, int]:
        if not self._httpd:
            return (self._host, self._port)
        return self._httpd.server_address

    def start(self):
        self._httpd = HTTPServer((self._host, self._port), _Handler)
        setattr(self._httpd, "on_pick", self._on_pick)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
