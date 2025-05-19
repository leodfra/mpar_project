from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)

# ── módulos propios ────────────────────────────────────────────────
from scraper_control import (
    ask_to_stop,
    get_result,
    new_job,
    set_result,
    stop_job,
)
from extraer_vacantes import scrape_mpar
from scrape_bumeran import scrap_jobs_bumeran
from scrape_computrabajo import scrape_computrabajo
from scrape_zonajobs import scrape_zonajobs

load_dotenv()
app = Flask(__name__, template_folder="templates")

SCRAPERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "mpar": scrape_mpar,
    "bumeran": scrap_jobs_bumeran,
    "zonajobs": scrape_zonajobs,
    "computrabajo": scrape_computrabajo,
}

# ─────────────────────────────── VISTAS ────────────────────────────
@app.get("/")
def index():
    return render_template("index.html")


@app.post("/scrape")
def start_scrape():
    data   = request.get_json(force=True)
    portal = data.get("sitio")
    fmt    = data.get("formato", "json")
    func   = SCRAPERS.get(portal)
    if not func:
        abort(400, "Portal no válido")

    job_id = new_job()

    def _worker(portal=portal, data=data, job_id=job_id):
        kwargs: dict[str, Any] = {"job_id": job_id}
        cargo = data.get("cargo", "").strip()
        ubic  = data.get("ubicacion", "").strip()

        if portal == "computrabajo":
            kwargs |= {"categoria": cargo, "lugar": ubic}
        elif portal in ("bumeran", "zonajobs", "mpar"):
            if cargo: kwargs["query"]    = cargo
            if ubic:  kwargs["location"] = ubic

        # -------- logging sin romper contexto --------
        app.logger.debug("Scraper %s – kwargs: %s", portal, kwargs)

        try:
            res = func(**kwargs)
        except Exception as exc:
            app.logger.exception("Scraper %s falló: %s", portal, exc)
            res = []                     # o lo que sea apropiado
        set_result(job_id, res)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify(job_id=job_id, fmt=fmt, portal=portal)


@app.post("/stop-scrape")
def stop_scrape():
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("job_id") or request.args.get("job_id")
    if not job_id:
        return {"ok": False, "error": "job_id requerido"}, 400
    stop_job(job_id)
    return {"ok": True}


@app.route("/download/<job_id>", methods=["HEAD", "GET"])
def download(job_id):
    data = get_result(job_id)

    # HEAD – ¿está listo el resultado?
    if request.method == "HEAD":
        if data is None:
            return ("", 404)
        if not data:                       # lista vacía ⇒ sin resultados
            return ("", 204)
        return ("", 200)

    # GET – enviar archivo
    if data is None:
        abort(404)
    if not data:                           # sin resultados
        return ("No data", 204)

    fmt = request.args.get("fmt", "json").lower()
    filename = f"{job_id}.{ 'xlsx' if fmt == 'excel' else 'json' }"
    return _excel_response(data, filename) if fmt == "excel" else _json_response(data, filename)


# ───────────────────────── helpers de respuesta ────────────────────
def _json_response(payload: list[dict[str, Any]], filename: str):
    buf = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode())
    buf.seek(0)
    return send_file(buf, as_attachment=True, mimetype="application/json", download_name=filename)


MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _excel_response(payload: list[dict[str, Any]], filename: str):
    df = pd.DataFrame(payload or [])  # evita error con lista vacía
    buf = io.BytesIO()

    for engine in ("xlsxwriter", "openpyxl"):
        try:
            with pd.ExcelWriter(buf, engine=engine) as writer:
                df.to_excel(writer, index=False, sheet_name="vacantes")
            break
        except ImportError as exc:
            current_app.logger.warning("Excel engine '%s' no disponible: %s", engine, exc)
            buf.seek(0)
            buf.truncate(0)
    else:
        raise RuntimeError(
            "Para generar Excel instalá:\n"
            "    pip install XlsxWriter   # o\n"
            "    pip install openpyxl"
        )

    buf.seek(0)
    return send_file(buf, as_attachment=True, mimetype=MIME_XLSX, download_name=filename)


# ─────────────────────────────── MAIN ──────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
