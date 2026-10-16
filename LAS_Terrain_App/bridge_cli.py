"""
bridge_cli.py
Cliente del puente de control de la GUI (usa solo la biblioteca estándar).

Uso:  python bridge_cli.py <comando> [json_args]
Ej.:  python bridge_cli.py status
      python bridge_cli.py classify '{"method":"grid","cell_size":2,"height_tol":0.5}'
      python bridge_cli.py screenshot
Comandos: status, log, view, screenshot, load, classify, surface, smooth, analyze
"""
import json
import os
import sys
import urllib.request

INFO = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bridge.json")


def call(cmd: str, args: dict | None = None, timeout: float = 620) -> dict:
    with open(INFO, encoding="utf-8") as f:
        info = json.load(f)
    req = urllib.request.Request(
        f"http://{info['host']}:{info['port']}/", method="POST",
        data=json.dumps({"cmd": cmd, "args": args or {}}).encode("utf-8"),
        headers={"X-Token": info["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        out = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})
    except FileNotFoundError:
        sys.exit("La GUI no está abierta (falta .bridge.json).")
    except OSError as e:
        sys.exit(f"No se pudo conectar con la GUI: {e}")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
