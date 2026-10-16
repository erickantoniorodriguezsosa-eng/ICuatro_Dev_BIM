# -*- coding: utf-8 -*-
"""Servidor local del Asistente BIM Vialidad. Uso: python server.py  ->  http://127.0.0.1:8765"""
import os
import sys
import json
import uuid
import time
import threading
import traceback
import datetime
import mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import ifc_engine as eng  # noqa: E402
import exports  # noqa: E402

DATA = os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")
REFS = os.path.join(ROOT, "_Referencias.IFC")
STATE_F = os.path.join(DATA, "estado.json")
HIST_F = os.path.join(DATA, "historial.json")
RULES_F = os.path.join(ROOT, "config", "reglas_mc.json")
STATIC_OK = {"index.html", "app.js"}
LOCK = threading.Lock()
JOBS = {}
RUNS = {}


def _jload(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _jsave(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def list_ifc_files():
    out = []
    for base in (REFS, UPLOADS):
        if not os.path.isdir(base):
            continue
        for dp, _, fs in os.walk(base):
            for f in sorted(fs):
                if f.lower().endswith(".ifc"):
                    p = os.path.join(dp, f)
                    d, src = eng.detect_discipline(f)
                    out.append({"path": p, "name": f, "mb": round(os.path.getsize(p) / 1e6, 1),
                                "discipline": d, "src": src, "origin": "referencia" if base == REFS else "subido"})
    return out


def allowed_path(p):
    p = os.path.abspath(p)
    return p.lower().endswith(".ifc") and os.path.isfile(p) and (
        p.startswith(os.path.abspath(REFS)) or p.startswith(os.path.abspath(UPLOADS)))


def apply_state(project, issues):
    st = _jload(STATE_F, {}).get(project, {})
    for it in issues:
        s = st.get(it["key"])
        if s:
            it["status"] = s.get("status", it["status"])
            if s.get("owner"):
                it["owner"] = s["owner"]


def history_entry(project, issues):
    hist = _jload(HIST_F, {})
    h = hist.setdefault(project, [])
    act = sum(1 for i in issues if i["status"] != "Resuelta")
    h.append({"fecha": datetime.datetime.now().strftime("%d-%m %H:%M"), "activas": act,
              "resueltas": len(issues) - act})
    hist[project] = h[-30:]
    _jsave(HIST_F, hist)
    return hist[project]


def run_job(job_id, body):
    job = JOBS[job_id]

    def log(msg, pct=None):
        job["log"].append(msg)
        job["log"] = job["log"][-60:]
        if pct is not None:
            job["pct"] = pct

    try:
        specs = []
        for f in body["files"]:
            if not allowed_path(f["path"]):
                raise ValueError(f"Ruta no permitida: {f['path']}")
            specs.append({"path": f["path"], "discipline": f.get("discipline") or None})
        project = (body.get("name") or "Cruce Vial").strip()
        rules = eng.load_rules(RULES_F)
        models = []
        for k, s in enumerate(specs):
            models.append(eng.load_model(s["path"], s["discipline"], os.path.join(ROOT, ".cache"), log))
            job["pct"] = (k + 1) / (len(specs) + 1)
        axis_csv = body.get("axis_csv")
        if axis_csv and not os.path.abspath(axis_csv).startswith(os.path.abspath(UPLOADS)):
            axis_csv = None
        res = eng.build_result(models, rules, axis_csv, bool(body.get("intra")), log, float(body.get("spacing") or 0.15))
        apply_state(project, res["issues"])
        res["history"] = history_entry(project, res["issues"])
        res["project"] = project
        run_id = uuid.uuid4().hex[:10]
        res["run"] = run_id
        RUNS[run_id] = {"models": models, "result": res, "project": project, "rules": rules}
        job.update(status="done", pct=1.0, run=run_id, result=res)
    except Exception as e:
        job.update(status="error", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])


class H(BaseHTTPRequestHandler):
    server_version = "AsistenteVial/1.0"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = unquote(u.path)
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(ROOT, "index.html"))
            if p == "/app.js":
                return self._file(os.path.join(ROOT, "app.js"))
            if p.startswith("/assets/"):
                f = os.path.abspath(os.path.join(ROOT, p.lstrip("/")))
                if f.startswith(os.path.join(ROOT, "assets")) and os.path.isfile(f):
                    return self._file(f)
                return self._send(404, {"error": "no existe"})
            if p == "/api/ping":
                return self._send(200, {"ok": True, "engine": "ifcopenshell"})
            if p == "/api/files":
                return self._send(200, list_ifc_files())
            if p == "/api/rules":
                return self._send(200, _jload(RULES_F, {}))
            if p.startswith("/api/job/"):
                j = JOBS.get(p.rsplit("/", 1)[1])
                if not j:
                    return self._send(404, {"error": "job"})
                return self._send(200, {k: v for k, v in j.items() if k != "result"} | (
                    {"result": j["result"]} if j.get("status") == "done" and q.get("full") else {}))
            if p == "/api/mesh":
                run = RUNS.get(q["run"][0])
                it = next((i for i in run["result"]["issues"] if i["id"] == q["id"][0]), None)
                if not it:
                    return self._send(404, {"error": "id"})
                ma, mb = run["models"][it["ia"]], run["models"][it["ib"]]
                data = eng.issue_mesh(ma, it["stepA"], mb, it["stepB"], it["point"])
                data["point"] = it["point"]
                return self._send(200, data)
            if p == "/api/export/bcf":
                run = RUNS[q["run"][0]]
                data = exports.to_bcf(run["result"], run["project"])
                return self._send(200, data, "application/zip",
                                  {"Content-Disposition": f'attachment; filename="Coordinacion_{run["project"][:30]}.bcfzip"'})
            if p == "/api/export/xlsx":
                run = RUNS[q["run"][0]]
                data = exports.to_xlsx(run["result"], run["project"], run["rules"])
                return self._send(200, data,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  {"Content-Disposition": f'attachment; filename="Libro_Coordinacion_{run["project"][:30]}.xlsx"'})
            return self._send(404, {"error": "ruta"})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _file(self, f):
        with open(f, "rb") as fh:
            data = fh.read()
        ct = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if ct.startswith("text/") or ct.endswith("javascript"):
            ct += "; charset=utf-8"
        self._send(200, data, ct)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/analyze":
                body = self._body()
                jid = uuid.uuid4().hex[:10]
                JOBS[jid] = {"status": "running", "pct": 0.0, "log": ["Iniciando…"], "started": time.time()}
                threading.Thread(target=run_job, args=(jid, body), daemon=True).start()
                return self._send(200, {"job": jid})
            if u.path == "/api/upload":
                name = os.path.basename(q["name"][0])
                if not name.lower().endswith((".ifc", ".csv")):
                    return self._send(400, {"error": "sólo .ifc o .csv"})
                os.makedirs(UPLOADS, exist_ok=True)
                dst = os.path.join(UPLOADS, name)
                n = int(self.headers.get("Content-Length") or 0)
                with open(dst, "wb") as fh:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        fh.write(chunk)
                        left -= len(chunk)
                d, src = eng.detect_discipline(name)
                return self._send(200, {"path": dst, "name": name, "discipline": d, "src": src,
                                        "mb": round(n / 1e6, 1)})
            if u.path == "/api/status":
                b = self._body()
                with LOCK:
                    st = _jload(STATE_F, {})
                    st.setdefault(b["project"], {})[b["key"]] = {
                        "status": b["status"], "owner": b.get("owner"),
                        "fecha": datetime.datetime.now().isoformat(timespec="seconds")}
                    _jsave(STATE_F, st)
                run = RUNS.get(b.get("run"))
                if run:
                    for it in run["result"]["issues"]:
                        if it["key"] == b["key"]:
                            it["status"] = b["status"]
                return self._send(200, {"ok": True})
            if u.path == "/api/rules":
                b = self._body()
                _jsave(RULES_F, b)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "ruta"})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    port = int(os.environ.get("PORT", "8765"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Asistente BIM Vialidad en http://127.0.0.1:{port}  (Ctrl+C para salir)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
