from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2.extras import Json, RealDictCursor

from src.utils.config_loader import default_config_path, load_config

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_storage_params: dict[str, Any] = {}
_initialized = False
_db_available = False
_RUNS_JSON_PATH = Path(__file__).resolve().parents[2] / "storage" / "runs.json"

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    decision TEXT NOT NULL,
    reason TEXT,
    suggestion TEXT,
    breakdown JSONB,
    ed_score DOUBLE PRECISION NOT NULL,
    sq_score DOUBLE PRECISION NOT NULL,
    m1 DOUBLE PRECISION,
    m2 DOUBLE PRECISION,
    model_version TEXT NOT NULL,
    dataset_snapshot_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    failure_tags JSONB,
    failure_severity TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs (timestamp);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    record_count INTEGER NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id SERIAL PRIMARY KEY,
    run_id TEXT,
    timestamp TIMESTAMPTZ NOT NULL,
    m1 DOUBLE PRECISION,
    m2 DOUBLE PRECISION,
    accuracy DOUBLE PRECISION,
    failure_distribution JSONB
);

CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics (timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    metric TEXT,
    severity TEXT,
    message TEXT
);
"""


def _migrate_failure_columns(conn: Any) -> None:
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS failure_tags JSONB")
        cur.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS failure_severity TEXT")
        cur.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS user_id TEXT")
        cur.execute("UPDATE runs SET user_id = 'anonymous' WHERE user_id IS NULL OR TRIM(user_id) = ''")
        cur.execute("ALTER TABLE runs ALTER COLUMN user_id SET DEFAULT 'anonymous'")
        cur.execute("ALTER TABLE runs ALTER COLUMN user_id SET NOT NULL")
        cur.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION NOT NULL DEFAULT 0.0")
        cur.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS breakdown JSONB")
        cur.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ")
        cur.execute("UPDATE runs SET timestamp = created_at WHERE timestamp IS NULL")
        cur.execute("ALTER TABLE runs ALTER COLUMN timestamp SET NOT NULL")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs (timestamp)")
        cur.execute("ALTER TABLE metrics ADD COLUMN IF NOT EXISTS failure_distribution JSONB")
    except Exception as e:
        logger.warning("failure column migration: %s", e)
    finally:
        cur.close()


def _ddl_statements() -> list[str]:
    parts: list[str] = []
    for block in _INIT_SQL.split(";"):
        s = block.strip()
        if s:
            parts.append(s + ";")
    return parts


def _storage_defaults() -> dict[str, Any]:
    return {
        "similarity_threshold": 0.90,
        "prefix_min_shorter_len": 20,
        "dedupe_recent_count": 100,
        "levenshtein_cap": 1200,
        "database_url": "",
    }


def _merge_storage_config(config: dict[str, Any] | None) -> dict[str, Any]:
    base = _storage_defaults()
    if isinstance(config, dict):
        s = config.get("storage")
        if isinstance(s, dict):
            for key in base:
                if key not in s:
                    continue
                if key in ("dedupe_recent_count", "prefix_min_shorter_len", "levenshtein_cap"):
                    base[key] = int(s[key])
                elif key == "similarity_threshold":
                    base[key] = float(s[key])
                else:
                    base[key] = str(s[key])
    return base


def get_db_connection() -> Any | None:
    """Return a new PostgreSQL connection, or None if unavailable."""
    try:
        p = _storage_params or _storage_defaults()
        database_url = str(os.environ.get("DATABASE_URL") or p.get("database_url") or "").strip()
        if not database_url:
            logger.warning("DATABASE_URL is not set; PostgreSQL connection unavailable")
            return None
        conn = psycopg2.connect(database_url, connect_timeout=10)
        return conn
    except Exception as e:
        logger.warning("PostgreSQL connection failed: %s", e)
        return None


def _normalize_prompt(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).lower().split())


def _levenshtein(a: str, b: str, cap: int) -> int:
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    if m > cap or n > cap:
        return _levenshtein(a[:cap], b[:cap], cap)
    prev = list(range(n + 1))
    cur = [0] * (n + 1)
    for i in range(1, m + 1):
        cur[0] = i
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev, cur = cur, prev
    return prev[n]


def _levenshtein_similarity(a: str, b: str, cap: int) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    mx = max(len(a), len(b))
    d = _levenshtein(a, b, cap)
    return 1.0 - (d / mx)


def _is_too_similar(a: str, b: str, sim_thresh: float, prefix_min: int, cap: int) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # Production fix: similarity/prefix dedupe dropped legitimate prompt edits
    # from live typing workflows (extension). Keep dedupe exact-match only.
    del sim_thresh, prefix_min, cap
    return False


def init_db(config: dict[str, Any] | None = None, db_path: Path | None = None) -> None:
    global _storage_params, _initialized, _db_available
    del db_path  # legacy SQLite param ignored
    with _lock:
        if config is not None:
            _storage_params = _merge_storage_config(config)
        elif not _storage_params:
            _storage_params = _storage_defaults()

        conn = get_db_connection()
        if conn is None:
            _db_available = False
            _initialized = True
            logger.warning("PostgreSQL init skipped (no connection)")
            return
        try:
            conn.autocommit = True
            cur = conn.cursor()
            for stmt in _ddl_statements():
                cur.execute(stmt)
            _migrate_failure_columns(conn)
            cur.close()
            _db_available = True
            logger.info("PostgreSQL storage ready using DATABASE_URL")
        except Exception as e:
            _db_available = False
            logger.warning("PostgreSQL schema init failed: %s", e)
        finally:
            conn.close()
        _initialized = True


def _ensure_init() -> None:
    if _initialized:
        return
    init_db(load_config(default_config_path()))


def _since_iso(time_range: str) -> str | None:
    tr = (time_range or "all").lower().strip()
    if tr == "all":
        return None
    now = datetime.now(timezone.utc)
    if tr == "1h":
        return (now - timedelta(hours=1)).isoformat()
    if tr == "24h":
        return (now - timedelta(hours=24)).isoformat()
    if tr == "7d":
        return (now - timedelta(days=7)).isoformat()
    return None


def _build_where(since: str | None, decision: str, user_id: str | None = None) -> tuple[str, list[Any]]:
    parts: list[str] = ["1=1"]
    params: list[Any] = []
    if since:
        parts.append("created_at >= %s")
        params.append(since)
    d = (decision or "all").lower().strip()
    if d in ("accept", "reject", "review"):
        parts.append("LOWER(TRIM(decision)) = %s")
        params.append(d)
    uid = str(user_id).strip() if user_id is not None else ""
    if uid:
        parts.append("user_id = %s")
        params.append(uid)
    return " AND ".join(parts), params


def _parse_ts_val(s: Any) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    t = str(s).strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    return datetime.fromisoformat(t)


def _decision_bucket_count(time_range: str) -> int:
    tr = (time_range or "all").lower().strip()
    return {"1h": 12, "24h": 24, "7d": 7, "all": 30}.get(tr, 24)


def _build_decision_time_buckets(
    rows: list[tuple[Any, str]],
    n_buckets: int,
) -> list[dict[str, Any]]:
    if not rows or n_buckets < 1:
        return []
    times: list[datetime] = []
    decs: list[str] = []
    for ts, dec in rows:
        try:
            times.append(_parse_ts_val(ts))
            decs.append(str(dec or "").lower().strip() or "reject")
        except (ValueError, TypeError):
            continue
    if not times:
        return []
    t_min = min(times)
    t_max = max(times)
    if t_max <= t_min:
        t_max = t_min + timedelta(seconds=1)
    span = (t_max - t_min).total_seconds()
    if span <= 0:
        span = 1.0
    buckets: list[dict[str, Any]] = []
    for i in range(n_buckets):
        t0 = t_min + timedelta(seconds=(span * i) / n_buckets)
        t1 = t_min + timedelta(seconds=(span * (i + 1)) / n_buckets)
        label = t0.strftime("%m/%d %H:%M") if span < 86400 * 2 else t0.strftime("%Y-%m-%d")
        acc = rej = rev = 0
        for j, tm in enumerate(times):
            if t0 <= tm < t1 or (i == n_buckets - 1 and tm <= t1):
                d = decs[j]
                if d == "accept":
                    acc += 1
                elif d == "review":
                    rev += 1
                else:
                    rej += 1
        buckets.append(
            {
                "label": label,
                "accept": acc,
                "reject": rej,
                "review": rev,
            }
        )
    return buckets


def _ts_to_iso(v: Any) -> str:
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    return str(v)


def _parse_failure_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if isinstance(x, str)]
    if isinstance(raw, str):
        try:
            j = json.loads(raw)
            if isinstance(j, list):
                return [str(x) for x in j if isinstance(x, str)]
        except Exception:
            return []
    return []


def _parse_breakdown(raw: Any) -> dict[str, float]:
    data: dict[str, Any] = {}
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            data = {}
    return {
        "clarity": float(data.get("clarity") or 0.0),
        "structure": float(data.get("structure") or 0.0),
        "actionability": float(data.get("actionability") or 0.0),
    }


def _ensure_runs_json_file() -> None:
    try:
        _RUNS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _RUNS_JSON_PATH.exists():
            _RUNS_JSON_PATH.write_text("[]", encoding="utf-8")
    except Exception as e:
        logger.warning("runs.json ensure failed: %s", e)


def _load_runs_json() -> list[dict[str, Any]]:
    _ensure_runs_json_file()
    try:
        raw = _RUNS_JSON_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception as e:
        logger.warning("runs.json read failed: %s", e)
    return []


def _save_runs_json(rows: list[dict[str, Any]]) -> bool:
    _ensure_runs_json_file()
    tmp = _RUNS_JSON_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_RUNS_JSON_PATH)
        return True
    except Exception as e:
        logger.warning("runs.json write failed: %s", e)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def _normalize_run_dict_for_json(run_data: dict[str, Any], *, rid: str, ts: datetime) -> dict[str, Any]:
    raw_prompt = run_data.get("prompt", "")
    prompt_text = str(raw_prompt) if raw_prompt is not None else ""
    response = str(run_data.get("response") or run_data.get("response_text") or "")
    decision = str(run_data.get("decision", "reject")).lower().strip()
    if decision not in ("accept", "reject", "review"):
        decision = "reject"
    reason = str(run_data.get("reason", "") or "")
    suggestion = str(run_data.get("suggestion", "") or "")
    ed = run_data.get("ed_score")
    if ed is None:
        ed = run_data.get("ed")
    sq = run_data.get("sq_score")
    if sq is None:
        sq = run_data.get("sq")
    try:
        ed_f = float(ed) if ed is not None else 0.0
        sq_f = float(sq) if sq is not None else 0.0
    except (TypeError, ValueError):
        ed_f, sq_f = 0.0, 0.0
    score_raw = run_data.get("score")
    try:
        score_v = float(score_raw) if score_raw is not None else (ed_f + sq_f) / 2.0
    except (TypeError, ValueError):
        score_v = (ed_f + sq_f) / 2.0
    breakdown = _parse_breakdown(run_data.get("breakdown"))

    m1_v: float | None = None
    m2_v: float | None = None
    if run_data.get("m1") is not None:
        try:
            m1_v = float(run_data["m1"])
        except (TypeError, ValueError):
            m1_v = None
    if run_data.get("m2") is not None:
        try:
            m2_v = float(run_data["m2"])
        except (TypeError, ValueError):
            m2_v = None

    ft_raw = run_data.get("failure_tags")
    if not isinstance(ft_raw, list):
        ft_raw = []
    fs_raw = run_data.get("failure_severity")
    failure_severity = str(fs_raw)[:64] if fs_raw is not None else ""

    model_version = str(run_data.get("model_version") or "").strip() or "v1.0"
    dataset_snapshot_id = str(run_data.get("dataset_snapshot_id") or "").strip() or "local-json"
    user_id = str(run_data.get("user_id") or "anonymous").strip() or "anonymous"

    created_at = _ts_to_iso(ts)
    return {
        "run_id": rid,
        "id": rid,
        "prompt": prompt_text,
        "response": response,
        "score": score_v,
        "decision": decision,
        "reason": reason,
        "suggestion": suggestion,
        "breakdown": breakdown,
        "scores": {"ed": ed_f, "sq": sq_f},
        "ed_score": ed_f,
        "sq_score": sq_f,
        "m1": m1_v,
        "m2": m2_v,
        "timestamp": created_at,
        "created_at": created_at,
        "model_version": model_version,
        "dataset_snapshot_id": dataset_snapshot_id,
        "user_id": user_id,
        "failure_tags": ft_raw,
        "failure_severity": failure_severity,
    }


def _filter_runs(
    rows: list[dict[str, Any]],
    time_range: str = "all",
    decision: str = "all",
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    since_iso = _since_iso(time_range)
    dec = (decision or "all").lower().strip()
    uid = str(user_id).strip() if user_id is not None else ""
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            ts = _parse_ts_val(row.get("created_at") or row.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if since_iso:
            try:
                if ts < _parse_ts_val(since_iso):
                    continue
            except (TypeError, ValueError):
                pass
        d = str(row.get("decision") or "").lower().strip()
        if dec in ("accept", "reject", "review") and d != dec:
            continue
        row_uid = str(row.get("user_id") or "anonymous").strip() or "anonymous"
        if uid and row_uid != uid:
            continue
        out.append(row)
    return out


def save_dataset_snapshot(record_count: int, description: str = "") -> str:
    _ensure_init()
    did = str(uuid.uuid4())
    ts = datetime.now(timezone.utc)
    desc = (description or "")[:4000]
    conn = get_db_connection()
    if conn is None:
        logger.warning("save_dataset_snapshot: no DB; returning id without persist")
        return did
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO datasets (id, created_at, record_count, description)
                VALUES (%s, %s, %s, %s)
                """,
                (did, ts, int(record_count), desc),
            )
        conn.commit()
    except Exception as e:
        logger.warning("save_dataset_snapshot failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
    return did


def save_model(name: str, version: str) -> str:
    _ensure_init()
    n = str(name or "default").strip() or "default"
    v = str(version or "").strip() or "v1.0"
    conn = get_db_connection()
    if conn is None:
        return str(uuid.uuid4())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id FROM models WHERE name = %s AND version = %s",
                (n, v),
            )
            row = cur.fetchone()
            if row:
                return str(row["id"])
            mid = str(uuid.uuid4())
            ts = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO models (id, name, version, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (mid, n, v, ts),
            )
        conn.commit()
        return mid
    except Exception as e:
        logger.warning("save_model failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return str(uuid.uuid4())
    finally:
        conn.close()


def _row_to_run_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row["id"],
        "prompt": row["prompt"],
        "response": row["response"],
        "decision": row["decision"],
        "reason": row["reason"],
        "suggestion": row["suggestion"],
        "breakdown": _parse_breakdown(row.get("breakdown")),
        "ed": row["ed_score"],
        "sq": row["sq_score"],
        "ed_score": row["ed_score"],
        "sq_score": row["sq_score"],
        "m1": row["m1"],
        "m2": row["m2"],
        "created_at": _ts_to_iso(row["created_at"]),
        "model_version": row["model_version"],
        "dataset_snapshot_id": row["dataset_snapshot_id"],
        "failure_tags": _parse_failure_tags(row.get("failure_tags")),
        "failure_severity": str(row.get("failure_severity") or "")
        if row.get("failure_severity") is not None
        else "",
    }


def get_runs_by_model(model_version: str) -> list[dict[str, Any]]:
    _ensure_init()
    if not _db_available:
        return []
    mv = str(model_version or "").strip()
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, prompt, response, decision, reason, suggestion,
                       breakdown, ed_score, sq_score, m1, m2, created_at, model_version, dataset_snapshot_id,
                       failure_tags, failure_severity
                FROM runs
                WHERE model_version = %s
                ORDER BY created_at DESC
                """,
                (mv,),
            )
            return [_row_to_run_dict(dict(r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("get_runs_by_model failed: %s", e)
        return []
    finally:
        conn.close()


def get_runs_by_dataset(dataset_snapshot_id: str) -> list[dict[str, Any]]:
    _ensure_init()
    if not _db_available:
        return []
    ds = str(dataset_snapshot_id or "").strip()
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, prompt, response, decision, reason, suggestion,
                       breakdown, ed_score, sq_score, m1, m2, created_at, model_version, dataset_snapshot_id,
                       failure_tags, failure_severity
                FROM runs
                WHERE dataset_snapshot_id = %s
                ORDER BY created_at DESC
                """,
                (ds,),
            )
            return [_row_to_run_dict(dict(r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("get_runs_by_dataset failed: %s", e)
        return []
    finally:
        conn.close()


def save_run(run_data: dict[str, Any]) -> bool:
    logger.warning("🔥 SAVE CALLED 🔥 %s", run_data)
    _ensure_init()
    if not isinstance(run_data, dict):
        return False

    raw_prompt = run_data.get("prompt", "")
    prompt_text = str(raw_prompt) if raw_prompt is not None else ""
    new_norm = _normalize_prompt(prompt_text)
    if not new_norm:
        return False

    rid = str(run_data.get("run_id") or run_data.get("id") or uuid.uuid4())
    ts_raw = run_data.get("created_at") or run_data.get("timestamp")
    if not ts_raw:
        ts = datetime.now(timezone.utc)
    else:
        try:
            ts = _parse_ts_val(ts_raw)
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)

    response = str(run_data.get("response") or run_data.get("response_text") or "")
    decision = str(run_data.get("decision", "reject"))
    reason = str(run_data.get("reason", "") or "")
    suggestion = str(run_data.get("suggestion", "") or "")

    ed = run_data.get("ed_score")
    if ed is None:
        ed = run_data.get("ed")
    sq = run_data.get("sq_score")
    if sq is None:
        sq = run_data.get("sq")
    try:
        ed_f = float(ed) if ed is not None else 0.0
        sq_f = float(sq) if sq is not None else 0.0
    except (TypeError, ValueError):
        ed_f, sq_f = 0.0, 0.0
    score_raw = run_data.get("score")
    try:
        score_v = float(score_raw) if score_raw is not None else (ed_f + sq_f) / 2.0
    except (TypeError, ValueError):
        score_v = (ed_f + sq_f) / 2.0
    breakdown = _parse_breakdown(run_data.get("breakdown"))

    m1_v: float | None = None
    m2_v: float | None = None
    if run_data.get("m1") is not None:
        try:
            m1_v = float(run_data["m1"])
        except (TypeError, ValueError):
            m1_v = None
    if run_data.get("m2") is not None:
        try:
            m2_v = float(run_data["m2"])
        except (TypeError, ValueError):
            m2_v = None

    sim = float(_storage_params["similarity_threshold"])
    pmin = int(_storage_params["prefix_min_shorter_len"])
    cap = int(_storage_params["levenshtein_cap"])
    n_recent = int(_storage_params["dedupe_recent_count"])

    model_version = str(run_data.get("model_version") or "").strip()
    dataset_snapshot_id = str(run_data.get("dataset_snapshot_id") or "").strip()
    if not model_version or not dataset_snapshot_id:
        if _db_available:
            logger.warning("save_run rejected: missing model_version or dataset_snapshot_id")
            return False
        model_version = model_version or "v1.0"
        dataset_snapshot_id = dataset_snapshot_id or "local-json"

    model_name = str(run_data.get("model_name") or "default").strip() or "default"
    user_id = str(run_data.get("user_id") or "anonymous").strip() or "anonymous"
    save_model(model_name, model_version)

    ft_raw = run_data.get("failure_tags")
    if ft_raw is not None and not isinstance(ft_raw, list):
        ft_raw = None
    fs_raw = run_data.get("failure_severity")
    failure_severity = str(fs_raw)[:64] if fs_raw is not None else None

    if not _db_available:
        logger.info(
            "save_run(json) before write run_id=%s decision=%s score=%.4f created_at=%s",
            rid,
            decision,
            score_v,
            _ts_to_iso(ts),
        )
        try:
            rows = _load_runs_json()
            recent = sorted(
                rows,
                key=lambda x: str(x.get("created_at") or x.get("timestamp") or ""),
                reverse=True,
            )[:n_recent]
            for prev in recent:
                pn = _normalize_prompt(str(prev.get("prompt") or ""))
                if pn and _is_too_similar(new_norm, pn, sim, pmin, cap):
                    logger.info("save_run(json) skipped duplicate prompt")
                    return False

            row = _normalize_run_dict_for_json(run_data, rid=rid, ts=ts)
            rows.append(row)
            ok = _save_runs_json(rows)
            if ok:
                logger.info("save_run(json) after write success run_id=%s total=%d", rid, len(rows))
            else:
                logger.warning("save_run(json) after write failed run_id=%s", rid)
            return ok
        except Exception as e:
            logger.warning("save_run(json) failed: %s", e)
            return False

    conn = get_db_connection()
    if conn is None:
        logger.warning("save_run failed: db connection unavailable")
        return False
    logger.info(
        "save_run(db) before write run_id=%s decision=%s score=%.4f created_at=%s",
        rid,
        decision,
        score_v,
        _ts_to_iso(ts),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prompt FROM runs ORDER BY created_at DESC LIMIT %s",
                (n_recent,),
            )
            for (prev_raw,) in cur.fetchall():
                pn = _normalize_prompt(str(prev_raw))
                if pn and _is_too_similar(new_norm, pn, sim, pmin, cap):
                    conn.rollback()
                    logger.info("save_run skipped duplicate prompt")
                    return False

            cur.execute(
                """
                INSERT INTO runs (
                    id, user_id, prompt, response, score, decision, reason, suggestion, breakdown,
                    ed_score, sq_score, m1, m2, created_at, timestamp,
                    model_version, dataset_snapshot_id,
                    failure_tags, failure_severity
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rid,
                    user_id,
                    prompt_text,
                    response,
                    score_v,
                    decision,
                    reason,
                    suggestion,
                    Json(breakdown),
                    ed_f,
                    sq_f,
                    m1_v,
                    m2_v,
                    ts,
                    ts,
                    model_version,
                    dataset_snapshot_id,
                    Json(ft_raw) if ft_raw is not None else None,
                    failure_severity,
                ),
            )
        conn.commit()
        logger.info("save_run(db) after write success run_id=%s", rid)
        return True
    except pg_errors.UniqueViolation:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("save_run integrity error for id=%s", rid)
        return False
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("save_run(db) failed: %s", e)
        return False
    finally:
        conn.close()


def get_recent_runs(
    limit: int,
    time_range: str = "all",
    decision: str = "all",
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_init()
    if not _db_available:
        rows = _filter_runs(_load_runs_json(), time_range=time_range, decision=decision, user_id=user_id)
        rows = sorted(
            rows,
            key=lambda x: str(x.get("created_at") or x.get("timestamp") or ""),
            reverse=True,
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        lim = max(0, int(limit))
        for row in rows:
            raw_s = str(row.get("prompt") or "")
            norm = _normalize_prompt(raw_s)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            d = str(row.get("decision") or "").lower().strip()
            if d not in ("accept", "reject", "review"):
                d = "reject"
            out.append(
                {
                    "prompt": raw_s.strip(),
                    "decision": d,
                    "run_id": str(row.get("run_id") or row.get("id") or ""),
                    "created_at": str(row.get("created_at") or row.get("timestamp") or ""),
                    "reason": str(row.get("reason") or ""),
                    "suggestion": str(row.get("suggestion") or ""),
                    "score": float(row.get("score") or 0.0),
                    "ed_score": float(row.get("ed_score")) if row.get("ed_score") is not None else None,
                    "sq_score": float(row.get("sq_score")) if row.get("sq_score") is not None else None,
                    "breakdown": _parse_breakdown(row.get("breakdown")),
                    "m1": float(row["m1"]) if row.get("m1") is not None else None,
                    "m2": float(row["m2"]) if row.get("m2") is not None else None,
                    "model_version": str(row.get("model_version") or ""),
                    "dataset_snapshot_id": str(row.get("dataset_snapshot_id") or ""),
                    "failure_tags": _parse_failure_tags(row.get("failure_tags")),
                    "failure_severity": str(row.get("failure_severity") or ""),
                }
            )
            if len(out) >= lim:
                break
        return out
    lim = max(0, int(limit))
    since = _since_iso(time_range)
    where, params = _build_where(since, decision, user_id=user_id)
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, prompt, decision, reason, suggestion, breakdown, score, ed_score, sq_score, m1, m2, created_at,
                       model_version, dataset_snapshot_id,
                       failure_tags, failure_severity
                FROM runs
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (*params, max(lim * 4, 100)),
            )
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("get_recent_runs failed: %s", e)
        return []
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        raw_s = str(row["prompt"] or "")
        norm = _normalize_prompt(raw_s)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        d = str(row["decision"] or "").lower().strip()
        if d not in ("accept", "reject", "review"):
            d = "reject"
        out.append(
            {
                "prompt": raw_s.strip(),
                "decision": d,
                "run_id": str(row["id"]),
                "created_at": _ts_to_iso(row["created_at"]),
                "reason": str(row.get("reason") or ""),
                "suggestion": str(row.get("suggestion") or ""),
                "score": float(row["score"]) if row.get("score") is not None else 0.0,
                "ed_score": float(row["ed_score"]) if row["ed_score"] is not None else None,
                "sq_score": float(row["sq_score"]) if row["sq_score"] is not None else None,
                "breakdown": _parse_breakdown(row.get("breakdown")),
                "m1": float(row["m1"]) if row["m1"] is not None else None,
                "m2": float(row["m2"]) if row["m2"] is not None else None,
                "model_version": str(row["model_version"] or ""),
                "dataset_snapshot_id": str(row["dataset_snapshot_id"] or ""),
                "failure_tags": _parse_failure_tags(row.get("failure_tags")),
                "failure_severity": str(row["failure_severity"] or "")
                if row.get("failure_severity") is not None
                else "",
            }
        )
        if len(out) >= lim:
            break
    return out


def get_stats(time_range: str = "all", decision: str = "all", user_id: str | None = None) -> dict[str, Any]:
    _ensure_init()
    empty = {
        "total_runs": 0,
        "accept_count": 0,
        "reject_count": 0,
        "review_count": 0,
        "accept_rate": 0.0,
        "reject_rate": 0.0,
        "review_rate": 0.0,
        "avg_ed": 0.0,
        "avg_sq": 0.0,
        "avg_m1": None,
        "avg_m2": None,
        "common_reasons": {},
        "most_common_reason": "",
        "ed_sq_series": [],
        "decision_time_buckets": [],
        "avg_clarity": 0.0,
        "avg_structure": 0.0,
        "avg_actionability": 0.0,
        "failure_distribution": {},
        "common_failure_types": [],
        "failure_severity_distribution": {"low": 0, "medium": 0, "high": 0},
    }
    if not _db_available:
        rows = _filter_runs(_load_runs_json(), time_range=time_range, decision=decision, user_id=user_id)
        total = len(rows)
        if total == 0:
            return empty
        accept_count = sum(1 for r in rows if str(r.get("decision", "")).lower().strip() == "accept")
        review_count = sum(1 for r in rows if str(r.get("decision", "")).lower().strip() == "review")
        reject_count = total - accept_count - review_count

        ed_vals = [float(r.get("ed_score", 0.0) or 0.0) for r in rows]
        sq_vals = [float(r.get("sq_score", 0.0) or 0.0) for r in rows]
        m1_vals = [float(r["m1"]) for r in rows if r.get("m1") is not None]
        m2_vals = [float(r["m2"]) for r in rows if r.get("m2") is not None]
        clarity_vals = [_parse_breakdown(r.get("breakdown")).get("clarity", 0.0) for r in rows]
        structure_vals = [_parse_breakdown(r.get("breakdown")).get("structure", 0.0) for r in rows]
        actionability_vals = [_parse_breakdown(r.get("breakdown")).get("actionability", 0.0) for r in rows]

        common: dict[str, int] = {}
        fd: dict[str, int] = {}
        sev_counts = {"low": 0, "medium": 0, "high": 0}
        ed_sq_series: list[dict[str, Any]] = []
        dec_rows: list[tuple[Any, str]] = []
        for r in sorted(rows, key=lambda x: str(x.get("created_at") or x.get("timestamp") or "")):
            reason = str(r.get("reason") or "").strip() or "(empty)"
            common[reason] = common.get(reason, 0) + 1
            ts = str(r.get("created_at") or r.get("timestamp") or "")
            ed_sq_series.append(
                {
                    "t": ts,
                    "ed": float(r.get("ed_score", 0.0) or 0.0),
                    "sq": float(r.get("sq_score", 0.0) or 0.0),
                }
            )
            dec_rows.append((ts, str(r.get("decision") or "reject")))
            for t in _parse_failure_tags(r.get("failure_tags")):
                fd[t] = fd.get(t, 0) + 1
            sev = str(r.get("failure_severity") or "").lower().strip()
            if sev in sev_counts:
                sev_counts[sev] += 1

        n_b = _decision_bucket_count(time_range)
        decision_time_buckets = _build_decision_time_buckets(dec_rows, n_b)
        common_ft = sorted(fd.items(), key=lambda x: -x[1])[:12]
        common_failure_types = [{"tag": a, "count": b} for a, b in common_ft]

        avg_m1 = (sum(m1_vals) / len(m1_vals)) if m1_vals else None
        avg_m2 = (sum(m2_vals) / len(m2_vals)) if m2_vals else None

        return {
            "total_runs": total,
            "accept_count": accept_count,
            "reject_count": reject_count,
            "review_count": review_count,
            "accept_rate": round(accept_count / total, 4),
            "reject_rate": round(reject_count / total, 4),
            "review_rate": round(review_count / total, 4),
            "avg_ed": round(sum(ed_vals) / len(ed_vals), 4) if ed_vals else 0.0,
            "avg_sq": round(sum(sq_vals) / len(sq_vals), 4) if sq_vals else 0.0,
            "avg_m1": round(avg_m1, 4) if avg_m1 is not None else None,
            "avg_m2": round(avg_m2, 4) if avg_m2 is not None else None,
            "common_reasons": common,
            "most_common_reason": max(common, key=common.get) if common else "",
            "ed_sq_series": ed_sq_series[-500:],
            "decision_time_buckets": decision_time_buckets,
            "avg_clarity": round(sum(clarity_vals) / len(clarity_vals), 4) if clarity_vals else 0.0,
            "avg_structure": round(sum(structure_vals) / len(structure_vals), 4) if structure_vals else 0.0,
            "avg_actionability": round(sum(actionability_vals) / len(actionability_vals), 4)
            if actionability_vals
            else 0.0,
            "failure_distribution": fd,
            "common_failure_types": common_failure_types,
            "failure_severity_distribution": sev_counts,
        }
    since = _since_iso(time_range)
    where, params = _build_where(since, decision, user_id=user_id)
    conn = get_db_connection()
    if conn is None:
        return empty
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM runs WHERE {where}", params)
            total = int((cur.fetchone() or {}).get("c") or 0)
            if total == 0:
                return empty

            accept_count = reject_count = review_count = 0
            cur.execute(
                f"""
                SELECT LOWER(TRIM(COALESCE(decision, ''))) AS d, COUNT(*) AS n
                FROM runs
                WHERE {where}
                GROUP BY LOWER(TRIM(COALESCE(decision, '')))
                """,
                params,
            )
            for r in cur.fetchall():
                d = str(r["d"] or "").strip().lower()
                c = int(r["n"])
                if d == "accept":
                    accept_count += c
                elif d == "review":
                    review_count += c
                else:
                    reject_count += c

            cur.execute(
                f"""
                SELECT AVG(ed_score) AS ae, AVG(sq_score) AS sq,
                       AVG(m1) AS am1, AVG(m2) AS am2,
                       AVG(COALESCE((breakdown->>'clarity')::double precision, 0.0)) AS a_clarity,
                       AVG(COALESCE((breakdown->>'structure')::double precision, 0.0)) AS a_structure,
                       AVG(COALESCE((breakdown->>'actionability')::double precision, 0.0)) AS a_actionability
                FROM runs WHERE {where}
                """,
                params,
            )
            avgs = cur.fetchone()
            ed_avg = float(avgs["ae"] or 0.0) if avgs and avgs.get("ae") is not None else 0.0
            sq_avg = float(avgs["sq"] or 0.0) if avgs and avgs.get("sq") is not None else 0.0
            avg_m1 = float(avgs["am1"]) if avgs and avgs.get("am1") is not None else None
            avg_m2 = float(avgs["am2"]) if avgs and avgs.get("am2") is not None else None
            avg_clarity = float(avgs["a_clarity"] or 0.0) if avgs and avgs.get("a_clarity") is not None else 0.0
            avg_structure = float(avgs["a_structure"] or 0.0) if avgs and avgs.get("a_structure") is not None else 0.0
            avg_actionability = (
                float(avgs["a_actionability"] or 0.0) if avgs and avgs.get("a_actionability") is not None else 0.0
            )

            common: dict[str, int] = {}
            cur.execute(
                f"""
                SELECT CASE
                    WHEN reason IS NULL OR TRIM(reason) = '' THEN '(empty)'
                    ELSE TRIM(reason)
                END AS rk, COUNT(*) AS n FROM runs WHERE {where} GROUP BY rk
                """,
                params,
            )
            for r in cur.fetchall():
                common[str(r["rk"])] = int(r["n"])

            cur.execute(
                f"""
                SELECT created_at, ed_score, sq_score
                FROM runs
                WHERE {where} AND ed_score IS NOT NULL AND sq_score IS NOT NULL
                ORDER BY created_at ASC
                LIMIT 500
                """,
                params,
            )
            ed_sq_series = [
                {
                    "t": _ts_to_iso(r["created_at"]),
                    "ed": float(r["ed_score"]),
                    "sq": float(r["sq_score"]),
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                f"SELECT created_at, decision FROM runs WHERE {where} ORDER BY created_at ASC",
                params,
            )
            dec_rows = [(r["created_at"], r["decision"]) for r in cur.fetchall()]

            fd: dict[str, int] = {}
            cur.execute(
                f"SELECT failure_tags FROM runs WHERE {where} AND failure_tags IS NOT NULL",
                params,
            )
            for r in cur.fetchall():
                for t in _parse_failure_tags(r.get("failure_tags")):
                    fd[t] = fd.get(t, 0) + 1

            cur.execute(
                """
                SELECT failure_distribution FROM metrics
                WHERE failure_distribution IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 25
                """
            )
            for r in cur.fetchall():
                dist = r.get("failure_distribution")
                if isinstance(dist, dict):
                    for k, v in dist.items():
                        try:
                            fd[str(k)] = fd.get(str(k), 0) + int(v)
                        except (TypeError, ValueError):
                            pass

            sev_counts = {"low": 0, "medium": 0, "high": 0}
            cur.execute(
                f"SELECT failure_severity FROM runs WHERE {where} AND failure_severity IS NOT NULL",
                params,
            )
            for r in cur.fetchall():
                s = str(r.get("failure_severity") or "").lower().strip()
                if s in sev_counts:
                    sev_counts[s] += 1

            common_ft = sorted(fd.items(), key=lambda x: -x[1])[:12]
            common_failure_types = [{"tag": a, "count": b} for a, b in common_ft]
    except Exception as e:
        logger.warning("get_stats failed: %s", e)
        return empty
    finally:
        conn.close()

    n_b = _decision_bucket_count(time_range)
    decision_time_buckets = _build_decision_time_buckets(dec_rows, n_b)

    return {
        "total_runs": total,
        "accept_count": accept_count,
        "reject_count": reject_count,
        "review_count": review_count,
        "accept_rate": round(accept_count / total, 4),
        "reject_rate": round(reject_count / total, 4),
        "review_rate": round(review_count / total, 4),
        "avg_ed": round(ed_avg, 4),
        "avg_sq": round(sq_avg, 4),
        "avg_m1": round(avg_m1, 4) if avg_m1 is not None else None,
        "avg_m2": round(avg_m2, 4) if avg_m2 is not None else None,
        "common_reasons": common,
        "most_common_reason": max(common, key=common.get) if common else "",
        "ed_sq_series": ed_sq_series,
        "decision_time_buckets": decision_time_buckets,
        "avg_clarity": round(avg_clarity, 4),
        "avg_structure": round(avg_structure, 4),
        "avg_actionability": round(avg_actionability, 4),
        "failure_distribution": fd,
        "common_failure_types": common_failure_types,
        "failure_severity_distribution": sev_counts,
    }


def get_all_runs() -> list[dict[str, Any]]:
    _ensure_init()
    if not _db_available:
        rows = _load_runs_json()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "run_id": str(r.get("run_id") or r.get("id") or ""),
                    "prompt": str(r.get("prompt") or ""),
                    "response": str(r.get("response") or ""),
                    "decision": str(r.get("decision") or "reject"),
                    "reason": str(r.get("reason") or ""),
                    "suggestion": str(r.get("suggestion") or ""),
                    "breakdown": _parse_breakdown(r.get("breakdown")),
                    "ed": float(r.get("ed_score") or 0.0),
                    "sq": float(r.get("sq_score") or 0.0),
                    "ed_score": float(r.get("ed_score") or 0.0),
                    "sq_score": float(r.get("sq_score") or 0.0),
                    "score": float(r.get("score") or 0.0),
                    "m1": float(r["m1"]) if r.get("m1") is not None else None,
                    "m2": float(r["m2"]) if r.get("m2") is not None else None,
                    "created_at": str(r.get("created_at") or r.get("timestamp") or ""),
                    "model_version": str(r.get("model_version") or ""),
                    "dataset_snapshot_id": str(r.get("dataset_snapshot_id") or ""),
                    "failure_tags": _parse_failure_tags(r.get("failure_tags")),
                    "failure_severity": str(r.get("failure_severity") or ""),
                }
            )
        return out
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, prompt, response, decision, reason, suggestion,
                       breakdown, ed_score, sq_score, m1, m2, created_at,
                       model_version, dataset_snapshot_id,
                       failure_tags, failure_severity
                FROM runs ORDER BY created_at ASC
                """
            )
            out: list[dict[str, Any]] = []
            for row in cur.fetchall():
                r = dict(row)
                out.append(
                    {
                        "run_id": r["id"],
                        "prompt": r["prompt"],
                        "response": r["response"],
                        "decision": r["decision"],
                        "reason": r["reason"],
                        "suggestion": r["suggestion"],
                        "breakdown": _parse_breakdown(r.get("breakdown")),
                        "ed": r["ed_score"],
                        "sq": r["sq_score"],
                        "ed_score": r["ed_score"],
                        "sq_score": r["sq_score"],
                        "m1": r["m1"],
                        "m2": r["m2"],
                        "created_at": _ts_to_iso(r["created_at"]),
                        "model_version": r["model_version"],
                        "dataset_snapshot_id": r["dataset_snapshot_id"],
                        "failure_tags": _parse_failure_tags(r.get("failure_tags")),
                        "failure_severity": str(r.get("failure_severity") or ""),
                    }
                )
            return out
    except Exception as e:
        logger.warning("get_all_runs failed: %s", e)
        return []
    finally:
        conn.close()


def load_runs() -> list[dict[str, Any]]:
    return get_all_runs()


def _coerce_ts(ts: str | datetime | None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return _parse_ts_val(ts)


def store_metric(
    run_id: str,
    timestamp: str | datetime | None,
    m1: float | None,
    m2: float | None,
    accuracy: float | None = None,
    failure_distribution: dict[str, int] | None = None,
) -> None:
    _ensure_init()
    if not _db_available:
        return
    ts = _coerce_ts(timestamp)
    conn = get_db_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO metrics (run_id, timestamp, m1, m2, accuracy, failure_distribution)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    str(run_id),
                    ts,
                    float(m1) if m1 is not None else None,
                    float(m2) if m2 is not None else None,
                    float(accuracy) if accuracy is not None else None,
                    Json(failure_distribution) if failure_distribution else None,
                ),
            )
        conn.commit()
    except Exception as e:
        logger.warning("store_metric failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def get_metrics_window(window_size: int) -> list[dict[str, Any]]:
    """Last ``window_size`` metric rows, oldest first (for drift)."""
    _ensure_init()
    if not _db_available:
        return []
    n = max(1, int(window_size))
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT run_id, timestamp, m1, m2, accuracy
                FROM (
                    SELECT run_id, timestamp, m1, m2, accuracy
                    FROM metrics
                    ORDER BY timestamp DESC
                    LIMIT %s
                ) sub
                ORDER BY timestamp ASC
                """,
                (n,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("get_metrics_window failed: %s", e)
        return []
    finally:
        conn.close()
    for r in rows:
        r["timestamp"] = _ts_to_iso(r["timestamp"])
    return rows


def get_metrics_over_time(
    limit: int = 500,
    *,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Metric samples in chronological order (time series)."""
    _ensure_init()
    if not _db_available:
        return []
    lim = max(1, int(limit))
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if since_iso:
                cur.execute(
                    """
                    SELECT run_id, timestamp, m1, m2, accuracy
                    FROM metrics
                    WHERE timestamp >= %s
                    ORDER BY timestamp ASC
                    LIMIT %s
                    """,
                    (since_iso, lim),
                )
            else:
                cur.execute(
                    """
                    SELECT run_id, timestamp, m1, m2, accuracy
                    FROM (
                        SELECT run_id, timestamp, m1, m2, accuracy
                        FROM metrics
                        ORDER BY timestamp DESC
                        LIMIT %s
                    ) sub
                    ORDER BY timestamp ASC
                    """,
                    (lim,),
                )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("get_metrics_over_time failed: %s", e)
        return []
    finally:
        conn.close()
    for r in rows:
        r["timestamp"] = _ts_to_iso(r["timestamp"])
    return rows


def list_metrics_recent(limit: int) -> list[dict[str, Any]]:
    """Most recent metric rows (newest first)."""
    _ensure_init()
    if not _db_available:
        return []
    lim = max(0, int(limit))
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT run_id, timestamp, m1, m2, accuracy
                FROM metrics
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (lim,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("list_metrics_recent failed: %s", e)
        return []
    finally:
        conn.close()
    for r in rows:
        r["timestamp"] = _ts_to_iso(r["timestamp"])
    return rows


def list_metrics_window(window_size: int) -> list[dict[str, Any]]:
    return get_metrics_window(window_size)


def save_alert(
    alert_id: str,
    timestamp: str | datetime,
    metric: str,
    severity: str,
    message: str,
) -> None:
    _ensure_init()
    if not _db_available:
        return
    ts = _coerce_ts(timestamp)
    conn = get_db_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (id, timestamp, metric, severity, message)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    timestamp = EXCLUDED.timestamp,
                    metric = EXCLUDED.metric,
                    severity = EXCLUDED.severity,
                    message = EXCLUDED.message
                """,
                (str(alert_id), ts, str(metric), str(severity), str(message)),
            )
        conn.commit()
    except Exception as e:
        logger.warning("save_alert failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def list_alerts_recent(limit: int) -> list[dict[str, Any]]:
    _ensure_init()
    if not _db_available:
        return []
    lim = max(0, int(limit))
    conn = get_db_connection()
    if conn is None:
        return []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, timestamp, metric, severity, message
                FROM alerts
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (lim,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("list_alerts_recent failed: %s", e)
        return []
    finally:
        conn.close()
    for r in rows:
        r["timestamp"] = _ts_to_iso(r["timestamp"])
    return rows


def init_storage(config: dict[str, Any], path: Path | None = None) -> None:
    del path
    init_db(config)


# Backward-compatible aliases
create_dataset_snapshot = save_dataset_snapshot
record_model = save_model
insert_metric = store_metric
insert_alert = save_alert


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db(load_config(default_config_path()))
    _sid = save_dataset_snapshot(1, "storage_demo")
    ok = save_run(
        {
            "run_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "prompt": "Why is sleep important for memory?",
            "response": "demo",
            "decision": "accept",
            "reason": "ok",
            "suggestion": "ok",
            "ed_score": 0.7,
            "sq_score": 0.7,
            "m1": 0.5,
            "m2": 0.6,
            "model_version": "v1.0",
            "dataset_snapshot_id": _sid,
            "model_name": "demo",
        }
    )
    logger.info("saved: %s total_runs=%s", ok, get_stats()["total_runs"])
