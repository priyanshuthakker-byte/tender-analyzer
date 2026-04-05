"""SQLite persistence: raw text + analysis JSON for re-run and audit."""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)
_lock = threading.Lock()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    with _lock:
        c = _connect(db_path)
        try:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS tenders (
                    id TEXT PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tender_id TEXT,
                    action TEXT NOT NULL,
                    detail_json TEXT,
                    user_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS prebid_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tender_id TEXT NOT NULL,
                    q_index INTEGER NOT NULL,
                    clause TEXT,
                    rfp_text TEXT,
                    query_text TEXT,
                    desired_clarification TEXT,
                    status TEXT NOT NULL DEFAULT 'drafted',
                    updated_at TEXT NOT NULL,
                    UNIQUE(tender_id, q_index)
                )
                """
            )
            c.commit()
        finally:
            c.close()


def _fallback_path(db_path: Path, tender_id: str) -> Path:
    return db_path.parent / "tenders_fallback" / f"{tender_id}.json"


def _write_fallback(
    db_path: Path,
    tender_id: str,
    raw_text: str,
    analysis: dict[str, Any],
    created_at: str,
    updated_at: str,
) -> None:
    p = _fallback_path(db_path, tender_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "id": tender_id,
        "raw_text": raw_text,
        "analysis": analysis,
        "created_at": created_at,
        "updated_at": updated_at,
        "storage": "json_fallback",
    }
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _read_fallback(db_path: Path, tender_id: str) -> dict[str, Any] | None:
    p = _fallback_path(db_path, tender_id)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "id": data["id"],
            "raw_text": data.get("raw_text", ""),
            "analysis": data.get("analysis", {}),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
        }
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def upsert_tender(
    db_path: Path,
    tender_id: str,
    raw_text: str,
    analysis: dict[str, Any],
) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(analysis, ensure_ascii=False)
    try:
        with _lock:
            c = _connect(db_path)
            try:
                row = c.execute("SELECT created_at FROM tenders WHERE id = ?", (tender_id,)).fetchone()
                created = row["created_at"] if row else now
                c.execute(
                    """
                    INSERT INTO tenders (id, raw_text, analysis_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        raw_text = excluded.raw_text,
                        analysis_json = excluded.analysis_json,
                        updated_at = excluded.updated_at
                    """,
                    (tender_id, raw_text, payload, created, now),
                )
                c.commit()
            finally:
                c.close()
        fp = _fallback_path(db_path, tender_id)
        if fp.exists():
            try:
                fp.unlink()
            except OSError:
                pass
    except (OSError, sqlite3.Error) as e:
        logger.error("SQLite upsert failed; writing JSON fallback: %s", e)
        row_fb = _read_fallback(db_path, tender_id)
        created = row_fb["created_at"] if row_fb else now
        analysis_fb = {**analysis, "storage_warning": "sqlite_unavailable; data in tenders_fallback JSON only"}
        _write_fallback(db_path, tender_id, raw_text, analysis_fb, created, now)


def get_tender(db_path: Path, tender_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    with _lock:
        c = _connect(db_path)
        try:
            row = c.execute(
                "SELECT id, raw_text, analysis_json, created_at, updated_at FROM tenders WHERE id = ?",
                (tender_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "raw_text": row["raw_text"],
                    "analysis": json.loads(row["analysis_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
        finally:
            c.close()
    return _read_fallback(db_path, tender_id)


def list_tenders(db_path: Path, limit: int = 200) -> list[dict[str, Any]]:
    init_db(db_path)
    with _lock:
        c = _connect(db_path)
        try:
            rows = c.execute(
                """
                SELECT id, analysis_json, updated_at FROM tenders
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            out = []
            seen: set[str] = set()
            for row in rows:
                seen.add(row["id"])
                aj = json.loads(row["analysis_json"])
                out.append(
                    {
                        "id": row["id"],
                        "updated_at": row["updated_at"],
                        "tender_no": aj.get("tender_no"),
                        "tender_name": aj.get("tender_name"),
                        "org_name": aj.get("org_name"),
                        "verdict": aj.get("verdict"),
                        "bid_submission_date": aj.get("bid_submission_date"),
                        "storage": "sqlite",
                    }
                )
        finally:
            c.close()

    fb_dir = db_path.parent / "tenders_fallback"
    if fb_dir.is_dir() and len(out) < limit:
        for p in sorted(fb_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.stem in seen:
                continue
            data = _read_fallback(db_path, p.stem)
            if not data:
                continue
            aj = data["analysis"]
            out.append(
                {
                    "id": p.stem,
                    "updated_at": data.get("updated_at", ""),
                    "tender_no": aj.get("tender_no"),
                    "tender_name": aj.get("tender_name"),
                    "org_name": aj.get("org_name"),
                    "verdict": aj.get("verdict"),
                    "bid_submission_date": aj.get("bid_submission_date"),
                    "storage": "json_fallback",
                }
            )
            seen.add(p.stem)
            if len(out) >= limit:
                break
    return out


def delete_tender(db_path: Path, tender_id: str) -> bool:
    init_db(db_path)
    removed = False
    with _lock:
        c = _connect(db_path)
        try:
            c.execute("DELETE FROM prebid_queries WHERE tender_id = ?", (tender_id,))
            cur = c.execute("DELETE FROM tenders WHERE id = ?", (tender_id,))
            c.commit()
            removed = cur.rowcount > 0
        finally:
            c.close()
    fp = _fallback_path(db_path, tender_id)
    if fp.is_file():
        try:
            fp.unlink()
            removed = True
        except OSError:
            pass
    return removed


def fetch_recent_analyses(db_path: Path, limit: int = 300) -> list[dict[str, Any]]:
    """Raw analysis dicts for portfolio insights (SQLite rows only)."""
    init_db(db_path)
    with _lock:
        c = _connect(db_path)
        try:
            rows = c.execute(
                """
                SELECT analysis_json FROM tenders
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [json.loads(row["analysis_json"]) for row in rows]
        finally:
            c.close()


def append_audit_log(
    db_path: Path,
    action: str,
    tender_id: str | None = None,
    detail: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    dj = json.dumps(detail or {}, ensure_ascii=False) if detail is not None else None
    try:
        with _lock:
            c = _connect(db_path)
            try:
                c.execute(
                    """
                    INSERT INTO audit_log (tender_id, action, detail_json, user_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (tender_id, action, dj, user_id, now),
                )
                c.commit()
            finally:
                c.close()
    except (OSError, sqlite3.Error) as e:
        logger.warning("audit_log insert skipped: %s", e)


def list_audit_log(db_path: Path, tender_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db(db_path)
    with _lock:
        c = _connect(db_path)
        try:
            if tender_id:
                rows = c.execute(
                    """
                    SELECT id, tender_id, action, detail_json, user_id, created_at
                    FROM audit_log WHERE tender_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (tender_id, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    """
                    SELECT id, tender_id, action, detail_json, user_id, created_at
                    FROM audit_log
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            out = []
            for row in rows:
                out.append(
                    {
                        "id": row["id"],
                        "tender_id": row["tender_id"],
                        "action": row["action"],
                        "detail": json.loads(row["detail_json"]) if row["detail_json"] else {},
                        "user_id": row["user_id"],
                        "created_at": row["created_at"],
                    }
                )
            return out
        finally:
            c.close()


def replace_prebid_from_analysis(db_path: Path, tender_id: str, analysis: dict[str, Any]) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    queries = analysis.get("prebid_queries") or []
    with _lock:
        c = _connect(db_path)
        try:
            c.execute("DELETE FROM prebid_queries WHERE tender_id = ?", (tender_id,))
            for i, q in enumerate(queries):
                if not isinstance(q, dict):
                    continue
                status = str(q.get("status", "drafted"))
                c.execute(
                    """
                    INSERT INTO prebid_queries (
                        tender_id, q_index, clause, rfp_text, query_text,
                        desired_clarification, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tender_id,
                        i,
                        q.get("clause"),
                        q.get("rfp_text"),
                        q.get("query"),
                        q.get("desired_clarification"),
                        status,
                        now,
                    ),
                )
            c.commit()
        finally:
            c.close()


def list_prebid_queries(db_path: Path, tender_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    with _lock:
        c = _connect(db_path)
        try:
            rows = c.execute(
                """
                SELECT q_index, clause, rfp_text, query_text, desired_clarification, status, updated_at
                FROM prebid_queries WHERE tender_id = ?
                ORDER BY q_index
                """,
                (tender_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            c.close()


def update_prebid_query_status(
    db_path: Path, tender_id: str, q_index: int, status: str
) -> bool:
    init_db(db_path)
    now = datetime.now(timezone.utc).isoformat()
    allowed = {"drafted", "sent", "closed", "withdrawn"}
    norm = str(status).strip().lower()
    if norm not in allowed:
        return False
    with _lock:
        c = _connect(db_path)
        try:
            cur = c.execute(
                """
                UPDATE prebid_queries SET status = ?, updated_at = ?
                WHERE tender_id = ? AND q_index = ?
                """,
                (norm, now, tender_id, q_index),
            )
            c.commit()
            return cur.rowcount > 0
        finally:
            c.close()


def update_analysis_prebid_status(db_path: Path, tender_id: str, analysis: dict[str, Any]) -> dict[str, Any]:
    """Sync prebid status from DB rows back into analysis JSON for export consistency."""
    rows = list_prebid_queries(db_path, tender_id)
    by_idx = {r["q_index"]: r["status"] for r in rows}
    out = dict(analysis)
    qs = out.get("prebid_queries")
    if isinstance(qs, list):
        for i, q in enumerate(qs):
            if isinstance(q, dict) and i in by_idx:
                q["status"] = by_idx[i]
    return out
