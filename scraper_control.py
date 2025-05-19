# scraper_control.py
# Mantiene el estado de cada job de scraping.

import uuid, threading
from typing import Dict

_JOBS: Dict[str, dict] = {}         # job_id -> {flag, result}

_LOCK = threading.Lock()

def new_job() -> str:
    """Registra un nuevo job y devuelve su id."""
    job_id = uuid.uuid4().hex
    with _LOCK:
        _JOBS[job_id] = {"stop": False, "result": None}
    return job_id

def ask_to_stop(job_id: str) -> bool:
    """El scraper lo llama cada X iteraciones."""
    with _LOCK:
        return _JOBS.get(job_id, {}).get("stop", True)

def stop_job(job_id: str) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["stop"] = True

def set_result(job_id: str, data):
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["result"] = data

def get_result(job_id: str):
    with _LOCK:
        return _JOBS.get(job_id, {}).get("result")
