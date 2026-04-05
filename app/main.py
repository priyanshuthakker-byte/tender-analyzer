from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import File, Form, Header, HTTPException, UploadFile
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.analyzer import analyze_tender
from app.config import Settings, load_company_profile
from app.db import (
    append_audit_log,
    delete_tender,
    get_tender,
    init_db,
    list_audit_log,
    list_prebid_queries,
    list_tenders,
    replace_prebid_from_analysis,
    update_analysis_prebid_status,
    update_prebid_query_status,
    upsert_tender,
)
from app.extract import read_document
from app.reports import write_report_bundle
from app.vault import scan_vault_hints

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"


def resolve_vault_path() -> Path | None:
    raw = settings.document_vault_path
    if not raw or not str(raw).strip():
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p if p.is_dir() else None


def build_corpus_from_paths(files: list[Path], max_chars: int, try_ocr: bool) -> str:
    """Concatenate extracted text with file markers."""
    parts: list[str] = []

    def priority(p: Path) -> int:
        n = p.name.lower()
        if n.endswith((".html", ".htm")):
            return 0
        if any(k in n for k in ("rfp", "nit", "tender", "bid", "main")):
            return 1
        if any(k in n for k in ("corrigendum", "addendum", "amendment", "corr_")):
            return 3
        return 2

    total = 0
    for p in sorted(files, key=priority):
        text = read_document(p, try_ocr=try_ocr)
        if not text.strip():
            continue
        cid = text.count("(cid:")
        if cid > 30 and cid / max(len(text), 1) > 0.01:
            logger.info("Skipping garbled file: %s", p.name)
            continue
        block = f"\n\n=== FILE: {p.name} ===\n{text}"
        if total + len(block) > max_chars:
            block = block[: max_chars - total]
        parts.append(block)
        total += len(block)
        if total >= max_chars:
            break
    return "".join(parts)


def collect_doc_paths_from_zip(raw: bytes, work_parent: Path) -> list[Path]:
    zdir = work_parent / f"_z_{uuid.uuid4().hex[:8]}"
    zdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        zf.extractall(zdir)
    paths: list[Path] = []
    for ext in ("*.pdf", "*.docx", "*.txt", "*.html", "*.htm", "*.xlsx", "*.xlsm"):
        paths.extend(zdir.rglob(ext))
    return paths


async def run_analyse(full_text: str) -> dict:
    profile = load_company_profile(settings)
    keys = settings.all_gemini_keys()

    def _work():
        return analyze_tender(full_text, profile, keys)

    return await asyncio.to_thread(_work)


def persist_analysis(
    tender_id: str,
    raw_text: str,
    payload: dict,
    user_id: str | None,
    *,
    event: str = "tender_analysed",
) -> dict:
    try:
        exports = write_report_bundle(PROJECT_ROOT, tender_id, payload)
        payload = {**payload, "export_paths": exports}
    except Exception as e:
        logger.warning("Report export failed: %s", e)
    upsert_tender(settings.database_path, tender_id, raw_text, payload)
    replace_prebid_from_analysis(settings.database_path, tender_id, payload)
    append_audit_log(
        settings.database_path,
        event,
        tender_id=tender_id,
        detail={"verdict": payload.get("verdict"), "confidence": payload.get("confidence_score")},
        user_id=user_id,
    )
    return payload


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db(settings.database_path)
    yield


app = FastAPI(
    title="Tender Analyser",
    description="Upload tender documents; structured PQ/TQ, pre-bid queries, checklist, governance, audit trail.",
    version="1.1.0",
    lifespan=_lifespan,
)


class PrebidStatusUpdate(BaseModel):
    q_index: int = Field(ge=0)
    status: str = Field(description="drafted | sent | closed | withdrawn")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ai_configured": bool(settings.all_gemini_keys()),
        "vault_configured": resolve_vault_path() is not None,
        "pdf_ocr_enabled": settings.enable_pdf_ocr,
    }


@app.get("/", response_class=HTMLResponse)
def root_page():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<p>UI missing. Use <code>/docs</code> for API.</p>")


@app.post("/api/analyse")
async def api_analyse(
    files: list[UploadFile] = File(...),
    tender_id: str = Form(""),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    """
    Upload PDF, DOCX, XLSX, TXT, HTML, or ZIP. Optional `tender_id` to upsert.
    Optional header `X-User-Id` for audit log entries.
    """
    if not files:
        raise HTTPException(400, "No files uploaded")

    tid = (tender_id or "").strip() or str(uuid.uuid4())
    work = PROJECT_ROOT / "data" / "_tmp_upload"
    work.mkdir(parents=True, exist_ok=True)
    doc_paths: list[Path] = []

    try:
        for upload in files:
            raw = await upload.read()
            if not raw:
                continue
            name = upload.filename or "upload"
            dest = work / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)

            lower = name.lower()
            if lower.endswith(".zip"):
                doc_paths.extend(collect_doc_paths_from_zip(raw, work))
            else:
                doc_paths.append(dest)

        seen: set[str] = set()
        unique: list[Path] = []
        for p in doc_paths:
            if p.name in seen:
                continue
            seen.add(p.name)
            unique.append(p)

        if not unique:
            raise HTTPException(
                400,
                "No readable documents found. Supported: PDF, DOCX, XLSX, TXT, HTML, ZIP.",
            )

        try_ocr = settings.enable_pdf_ocr
        all_text = build_corpus_from_paths(unique, settings.max_upload_chars, try_ocr)
        if len(all_text.strip()) < 200:
            raise HTTPException(
                400,
                "Extracted text is too short. For scanned PDFs install OCR deps (see README).",
            )

        result = await run_analyse(all_text)
        if result.get("error"):
            err_payload = {**result, "tender_id": tid}
            upsert_tender(settings.database_path, tid, all_text, err_payload)
            append_audit_log(
                settings.database_path,
                "tender_analyse_failed",
                tender_id=tid,
                detail={"error": result.get("error")},
                user_id=x_user_id,
            )
            raise HTTPException(502, result["error"])

        payload = persist_analysis(tid, all_text, {**result, "tender_id": tid}, x_user_id)
        return {"tender_id": tid, "analysis": payload}
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/tenders/{tender_id}/reanalyse")
async def api_reanalyse(
    tender_id: str,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    row = get_tender(settings.database_path, tender_id)
    if not row:
        raise HTTPException(404, "Tender not found")
    text = row.get("raw_text") or ""
    if len(text.strip()) < 200:
        raise HTTPException(400, "Saved text missing or too short; upload again with /api/analyse")

    result = await run_analyse(text)
    if result.get("error"):
        upsert_tender(
            settings.database_path,
            tender_id,
            text,
            {**result, "tender_id": tender_id},
        )
        append_audit_log(
            settings.database_path,
            "tender_reanalyse_failed",
            tender_id=tender_id,
            detail={"error": result.get("error")},
            user_id=x_user_id,
        )
        raise HTTPException(502, result["error"])

    payload = persist_analysis(
        tender_id,
        text,
        {**result, "tender_id": tender_id},
        x_user_id,
        event="tender_reanalysed",
    )
    return {"tender_id": tender_id, "analysis": payload}


@app.get("/api/tenders")
def api_list_tenders():
    return {"tenders": list_tenders(settings.database_path)}


@app.get("/api/tenders/{tender_id}")
def api_get_tender(tender_id: str):
    row = get_tender(settings.database_path, tender_id)
    if not row:
        raise HTTPException(404, "Not found")
    analysis = update_analysis_prebid_status(
        settings.database_path, tender_id, row["analysis"]
    )
    return {
        "tender_id": row["id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "analysis": analysis,
    }


@app.get("/api/tenders/{tender_id}/dashboard")
def api_dashboard(tender_id: str):
    row = get_tender(settings.database_path, tender_id)
    if not row:
        raise HTTPException(404, "Not found")
    analysis = update_analysis_prebid_status(
        settings.database_path, tender_id, row["analysis"]
    )
    vault = resolve_vault_path()
    hints = scan_vault_hints(vault, analysis.get("submission_checklist") or [])
    prebid = list_prebid_queries(settings.database_path, tender_id)
    return {
        "tender_id": tender_id,
        "overview": {
            "tender_no": analysis.get("tender_no"),
            "tender_name": analysis.get("tender_name"),
            "org_name": analysis.get("org_name"),
            "portal": analysis.get("portal"),
            "bid_submission_date": analysis.get("bid_submission_date"),
            "verdict": analysis.get("verdict"),
            "verdict_display": (analysis.get("overall_verdict") or {}).get("verdict_display"),
            "confidence_score": analysis.get("confidence_score"),
            "confidence_basis": analysis.get("confidence_basis"),
        },
        "pq_criteria": analysis.get("pq_criteria", []),
        "tq_criteria": analysis.get("tq_criteria", []),
        "prebid_queries": prebid,
        "submission_checklist": analysis.get("submission_checklist", []),
        "risk_highlights": analysis.get("risk_highlights", []),
        "governance_report": analysis.get("governance_report", {}),
        "export_paths": analysis.get("export_paths", {}),
        "vault_hints": hints,
        "notes": analysis.get("notes", []),
        "action_items": analysis.get("action_items", []),
    }


@app.patch("/api/tenders/{tender_id}/prebid")
def api_update_prebid(
    tender_id: str,
    body: PrebidStatusUpdate,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    if not get_tender(settings.database_path, tender_id):
        raise HTTPException(404, "Tender not found")
    ok = update_prebid_query_status(
        settings.database_path, tender_id, body.q_index, body.status
    )
    if not ok:
        raise HTTPException(404, "Pre-bid row not found for this index")
    append_audit_log(
        settings.database_path,
        "prebid_status_updated",
        tender_id=tender_id,
        detail={"q_index": body.q_index, "status": body.status},
        user_id=x_user_id,
    )
    row = get_tender(settings.database_path, tender_id)
    analysis = update_analysis_prebid_status(settings.database_path, tender_id, row["analysis"])
    upsert_tender(settings.database_path, tender_id, row["raw_text"], analysis)
    return {"status": "ok", "q_index": body.q_index, "status": body.status}


@app.get("/api/tenders/{tender_id}/audit")
def api_tender_audit(tender_id: str, limit: int = 100):
    if not get_tender(settings.database_path, tender_id):
        raise HTTPException(404, "Tender not found")
    return {"entries": list_audit_log(settings.database_path, tender_id, limit)}


@app.get("/api/audit")
def api_audit_global(limit: int = 200):
    return {"entries": list_audit_log(settings.database_path, None, limit)}


@app.delete("/api/tenders/{tender_id}")
def api_delete_tender(
    tender_id: str,
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    if not get_tender(settings.database_path, tender_id):
        raise HTTPException(404, "Not found")
    append_audit_log(
        settings.database_path,
        "tender_deleted",
        tender_id=tender_id,
        detail={},
        user_id=x_user_id,
    )
    delete_tender(settings.database_path, tender_id)
    return {"status": "deleted", "tender_id": tender_id}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
