from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field, StrictStr

from backend.auth.email_auth import init_auth_db, router as auth_router
from backend.db.storage import get_recent_runs, get_stats, init_db, list_alerts_recent, save_run

from src.features.analysis.drift.time_series import get_drift_panel
from src.features.logging.scheduler.scheduler import get_scheduler_status, start_scheduler, stop_scheduler
from src.features.logging.scheduler.triggers import trigger_manual_run

from src.pipeline import run_pipeline
from src.services.model_adapter import ModelAdapter
from src.utils.config_loader import load_config
from src.utils.paths import project_root

logger = logging.getLogger("api_server")

_BASE = project_root()
_CONFIG_PATH = str(_BASE / "configs" / "base.yaml")
CONFIG: dict[str, Any] = load_config(_CONFIG_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    logger.info("Static directory configured: %s (exists=%s)", STATIC_DIR, STATIC_DIR.is_dir())
    try:
        init_db(CONFIG)
    except Exception:
        logger.exception("Storage init failed; endpoints use degraded empty results")
    try:
        init_auth_db()
    except Exception:
        logger.exception("Auth DB init failed")
    app.state.config = CONFIG
    app.state.model = ModelAdapter()
    start_scheduler(CONFIG, app.state.model)
    logger.info("Application started")
    yield
    stop_scheduler()
    logger.info("Application shutdown")


def _api_section() -> dict[str, Any]:
    a = CONFIG.get("api")
    return a if isinstance(a, dict) else {}


app = FastAPI(lifespan=lifespan)
_api = _api_section()

STATIC_DIR = Path(__file__).resolve().parents[2] / "public" / "static"

print("STATIC DIR:", STATIC_DIR)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])


class AnalyzeRequest(BaseModel):
    prompt: StrictStr | None = Field(
        None,
        description="User prompt text",
        max_length=10000,
    )
    text: StrictStr | None = Field(
        None,
        description="Legacy prompt text field",
        max_length=10000,
    )


def _resolve_prompt_text(data: AnalyzeRequest) -> str:
    raw = data.prompt if data.prompt is not None else data.text
    if raw is None:
        raise TypeError("prompt must be a string")
    text = raw.strip()
    if not text:
        raise ValueError("prompt must not be empty")
    return text


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        if parsed != parsed:  # NaN guard
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _build_feedback(reason: str, suggestion: str) -> list[str]:
    out: list[str] = []
    r = str(reason or "").strip()
    s = str(suggestion or "").strip()
    if r:
        out.append(r)
    if s:
        out.append(s)
    if not out:
        out.append("No feedback available")
    return out


def _normalized_error_payload(message: str, *, prompt: str = "", detail: str | None = None) -> dict[str, Any]:
    reason = str(message or "Analysis failed")
    feedback = _build_feedback(reason, "Check input and retry")
    payload: dict[str, Any] = {
        "success": False,
        "error": reason,
        "score": 0.0,
        "feedback": feedback,
        "run_id": "",
        "created_at": "",
        "prompt": prompt,
        "response": "",
        "decision": "review",
        "reason": reason,
        "suggestion": "Check input and retry",
        "ed_score": 0.0,
        "sq_score": 0.0,
        "breakdown": {
            "clarity": 0.0,
            "structure": 0.0,
            "actionability": 0.0,
        },
        "m1": None,
        "m2": None,
        "model_version": "",
        "dataset_snapshot_id": "",
        "failure_tags": [],
        "failure_severity": "error",
        "scores": {"ed": 0.0, "sq": 0.0},
        "prompt_logged": False,
        "processing_ms": 0.0,
    }
    if detail:
        payload["detail"] = detail
    return payload


def _normalized_success_payload(result: dict[str, Any], prompt_logged: bool, processing_ms: float) -> dict[str, Any]:
    ed = _to_float(result.get("ed_score"), 0.0)
    sq = _to_float(result.get("sq_score"), 0.0)
    score = round((ed + sq) / 2.0, 6)
    reason = str(result.get("reason", ""))
    suggestion = str(result.get("suggestion", ""))
    breakdown_raw = result.get("breakdown")
    breakdown = breakdown_raw if isinstance(breakdown_raw, dict) else {}
    clarity = _to_float(breakdown.get("clarity"), 0.0)
    structure = _to_float(breakdown.get("structure"), 0.0)
    actionability = _to_float(breakdown.get("actionability"), 0.0)
    payload: dict[str, Any] = {
        **result,
        "success": True,
        "error": None,
        "score": score,
        "feedback": _build_feedback(reason, suggestion),
        "scores": {
            "ed": ed,
            "sq": sq,
        },
        "breakdown": {
            "clarity": round(clarity, 4),
            "structure": round(structure, 4),
            "actionability": round(actionability, 4),
        },
        "prompt_logged": bool(prompt_logged),
        "processing_ms": round(processing_ms, 3),
    }
    return payload


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    msg = "Invalid request body"
    if errs:
        msg = str(errs[0].get("msg", msg))
    print(f"Validation failed for {request.url.path}: {msg}")
    logger.info("Validation failed: %s", msg)
    return JSONResponse(status_code=400, content=_normalized_error_payload(msg))


@app.get("/")
def root():
    return {"status": "API running"}


def _tr(v: str) -> str:
    t = (v or "all").lower().strip()
    return t if t in ("1h", "24h", "7d", "all") else "all"


def _dec(v: str) -> str:
    t = (v or "all").lower().strip()
    return t if t in ("accept", "reject", "review", "all") else "all"


def _effective_user_id(user_id_query: str | None, x_user_id: str | None) -> str:
    """Prefer x-user-id header so query params cannot override another user's identity."""
    h = (x_user_id or "").strip()
    if h:
        return h
    q = (user_id_query or "").strip()
    return q if q else "anonymous"


@app.get("/stats")
def stats(
    time_range: str = Query("all", alias="range"),
    decision: str = Query("all"),
    user_id: str | None = Query(None),
    x_user_id: str | None = Header(None, alias="x-user-id"),
):
    uid = _effective_user_id(user_id, x_user_id)
    print(f"Incoming request: GET /stats range={time_range} decision={decision} user_id={uid}")
    logger.info("GET /stats range=%s decision=%s user_id=%s", time_range, decision, uid)
    base = get_stats(_tr(time_range), _dec(decision), user_id=uid)
    panel = get_drift_panel(CONFIG, user_id=uid)
    print(f"Response /stats total_runs={base.get('total_runs', 0)} user_id={uid}")
    logger.info("GET /stats response total_runs=%s user_id=%s", base.get("total_runs", 0), uid)
    return {**base, **panel}


@app.get("/alerts")
def list_alerts_endpoint(limit: int = Query(50, ge=1, le=200)):
    return list_alerts_recent(limit)


@app.get("/scheduler/status")
def scheduler_status():
    return {"status": get_scheduler_status()}


@app.post("/trigger/evaluate")
def trigger_evaluate(request: Request):
    cfg: dict[str, Any] = request.app.state.config
    model: ModelAdapter = request.app.state.model
    result = trigger_manual_run(cfg, model)
    if result is None:
        return JSONResponse(
            status_code=409,
            content={"error": "evaluation busy"},
        )
    return result


@app.get("/recent")
def recent(
    limit: int | None = Query(None, ge=1, le=500),
    time_range: str = Query("all", alias="range"),
    decision: str = Query("all"),
    user_id: str | None = Query(None),
    x_user_id: str | None = Header(None, alias="x-user-id"),
):
    lim = limit if limit is not None else int(_api.get("recent_runs_limit", 20))
    uid = _effective_user_id(user_id, x_user_id)
    return get_recent_runs(lim, _tr(time_range), _dec(decision), user_id=uid)


@app.get("/dashboard")
def dashboard(request: Request):
    user_id = (request.headers.get("x-user-id") or "anonymous").strip()
    logger.info("GET /dashboard user_id=%s", user_id)
    name = str(_api.get("dashboard_file", "dashboard.html"))
    path = _BASE / "public" / name
    if not path.is_file():
        return {"error": "dashboard not found", "path": str(path)}
    return FileResponse(path, media_type="text/html")


@app.post("/analyze")
def analyze(data: AnalyzeRequest, request: Request):
    logger.warning("🔥 ANALYZE HIT FROM EXTENSION 🔥 %s", data.model_dump())
    cfg: dict[str, Any] = request.app.state.config
    model: ModelAdapter = request.app.state.model
    started_at = time.perf_counter()
    try:
        prompt_text = _resolve_prompt_text(data)
    except (TypeError, ValueError) as exc:
        print(f"Incoming request: POST /analyze invalid body={data.model_dump()}")
        logger.info("POST /analyze invalid body=%s", data.model_dump())
        return JSONResponse(status_code=400, content=_normalized_error_payload(str(exc)))

    request_dump = data.model_dump()
    payload_source = "prompt" if data.prompt is not None else ("text" if data.text is not None else "none")
    logger.info(
        "ANALYZE INPUT source=%s payload=%s",
        payload_source,
        request_dump,
    )
    user_id = (request.headers.get("x-user-id") or "anonymous").strip()
    print(f"Incoming request: POST /analyze prompt={prompt_text[:120]!r} user_id={user_id}")
    body_preview = prompt_text[:200] + ("..." if len(prompt_text) > 200 else "")
    logger.info(
        "POST /analyze input text_len=%d preview=%r user_id=%s",
        len(prompt_text),
        body_preview,
        user_id,
    )

    try:
        logger.info("POST /analyze processing started")
        result, prompt_logged = run_pipeline(
            prompt_text,
            config=cfg,
            model=model,
            persist=True,
            user_id=user_id,
        )
        logger.warning("🔥 SAVE RESULT 🔥 %s", result)
        logger.info(
            "BEFORE SAVE computed_result run_id=%s decision=%s ed=%s sq=%s reason=%r",
            result.get("run_id"),
            result.get("decision"),
            result.get("ed_score"),
            result.get("sq_score"),
            str(result.get("reason") or "")[:200],
        )
        logger.info(
            "SAVE RESULT run_id=%s prompt_logged=%s",
            result.get("run_id"),
            prompt_logged,
        )
        if not prompt_logged:
            logger.error(
                "SAVE FAILED run_id=%s prompt_preview=%r decision=%s",
                result.get("run_id"),
                prompt_text[:200],
                result.get("decision"),
            )

        # Step 6 debug hook: optional forced save to isolate input-dependent failures.
        if str(os.environ.get("ANALYZE_FORCE_TEST_SAVE", "")).strip() == "1":
            forced_ts = datetime.now(timezone.utc).isoformat()
            forced_ok = save_run(
                {
                    "run_id": str(uuid.uuid4()),
                    "created_at": forced_ts,
                    "prompt": "FORCED TEST",
                    "response": "",
                    "score": 0.9,
                    "decision": "accept",
                    "reason": "forced test",
                    "suggestion": "forced test",
                    "ed_score": 0.9,
                    "sq_score": 0.9,
                    "m1": None,
                    "m2": None,
                    "model_version": str(result.get("model_version") or "v1.0"),
                    "dataset_snapshot_id": str(result.get("dataset_snapshot_id") or "forced-test"),
                    "failure_tags": [],
                    "failure_severity": "low",
                    "model_name": "forced-debug",
                    "user_id": user_id,
                }
            )
            logger.info("FORCED TEST SAVE RESULT success=%s", forced_ok)

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        response = _normalized_success_payload(result, prompt_logged, elapsed_ms)
        print(f"Response /analyze decision={response.get('decision')} scores={response.get('scores')}")
        logger.info(
            "POST /analyze output decision=%s score=%.3f prompt_logged=%s processing_ms=%.2f",
            response.get("decision"),
            _to_float(response.get("score"), 0.0),
            prompt_logged,
            elapsed_ms,
        )
        return response
    except Exception as exc:
        print(f"POST /analyze failed: {exc!r}")
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        logger.exception("POST /analyze failed after %.2fms", elapsed_ms)
        return JSONResponse(
            status_code=500,
            content=_normalized_error_payload(
                "Analysis failed",
                prompt=prompt_text,
                detail=str(exc),
            ),
        )
