"""
bridge.py
Puente de control local: servidor HTTP dentro de la GUI para que una herramienta externa
(Claude Code) consulte y comande la app abierta.

Seguridad: escucha SOLO en 127.0.0.1, puerto aleatorio, exige token (cabecera X-Token).
Host y token se escriben en .bridge.json (se regenera en cada arranque).
Los comandos son una lista cerrada (ver MainWindow._bridge_dispatch); no hay eval/exec.
"""
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

INFO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bridge.json")
TIMEOUT_S = 600


class Request:
    def __init__(self, cmd: str, args: dict):
        self.cmd, self.args = cmd, args
        self.event = threading.Event()
        self.result: dict = {}

    def finish(self, ok: bool, **data):
        self.result = {"ok": ok, **data}
        self.event.set()


class Bridge(QObject):
    request = Signal(object)  # se entrega al hilo de UI (conexión en cola)

    def __init__(self):
        super().__init__()
        self.token = secrets.token_hex(16)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # sin ruido en consola
                pass

            def _reply(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.headers.get("X-Token") != bridge.token:
                    return self._reply(403, {"ok": False, "error": "token inválido"})
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(n) or b"{}")
                    cmd = str(payload["cmd"])
                    args = payload.get("args") or {}
                except Exception as e:
                    return self._reply(400, {"ok": False, "error": f"petición inválida: {e}"})
                req = Request(cmd, args)
                bridge.request.emit(req)
                if not req.event.wait(TIMEOUT_S):
                    return self._reply(504, {"ok": False, "error": "timeout esperando a la GUI"})
                self._reply(200, req.result)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        with open(INFO_FILE, "w", encoding="utf-8") as f:
            json.dump({"host": "127.0.0.1", "port": self.port, "token": self.token,
                       "pid": os.getpid()}, f)

    def stop(self):
        self.httpd.shutdown()
        try:
            os.remove(INFO_FILE)
        except OSError:
            pass
