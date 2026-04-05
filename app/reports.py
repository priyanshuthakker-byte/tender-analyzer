"""Persist reproducible exports under data/reports/{tender_id}/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def report_dir(base: Path, tender_id: str) -> Path:
    d = base / "data" / "reports" / tender_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_report_bundle(project_root: Path, tender_id: str, analysis: dict[str, Any]) -> dict[str, str]:
    """
    Write JSON + markdown outline; optional submission_pack.pdf via reportlab.
    Returns map of logical name -> file path (relative to project_root).
    """
    out: dict[str, str] = {}
    rd = report_dir(project_root, tender_id)
    stamp = datetime.now(timezone.utc).isoformat()

    overview = {
        "tender_id": tender_id,
        "exported_at": stamp,
        "tender_no": analysis.get("tender_no"),
        "tender_name": analysis.get("tender_name"),
        "org_name": analysis.get("org_name"),
        "verdict": analysis.get("verdict"),
        "verdict_display": (analysis.get("overall_verdict") or {}).get("verdict_display"),
        "confidence_score": analysis.get("confidence_score"),
        "bid_submission_date": analysis.get("bid_submission_date"),
    }
    p_over = rd / "overview.json"
    p_over.write_text(json.dumps(overview, indent=2, ensure_ascii=False), encoding="utf-8")
    out["overview"] = str(p_over.relative_to(project_root))

    p_full = rd / "full_analysis.json"
    p_full.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    out["full_analysis"] = str(p_full.relative_to(project_root))

    lines = [
        f"# Submission pack outline — {tender_id}",
        f"Generated: {stamp}",
        "",
        "## Tender overview",
        f"- **No:** {analysis.get('tender_no', '—')}",
        f"- **Title:** {analysis.get('tender_name', '—')}",
        f"- **Org:** {analysis.get('org_name', '—')}",
        f"- **Verdict:** {analysis.get('verdict', '—')} (confidence {analysis.get('confidence_score', '—')})",
        "",
        "## Pre-bid queries (link to registered items in API / DB)",
        "",
    ]
    for i, q in enumerate(analysis.get("prebid_queries") or []):
        if isinstance(q, dict):
            lines.append(f"{i+1}. **{q.get('clause', '—')}** — status: {q.get('status', 'drafted')}")
            lines.append(f"   - Query: {q.get('query', '')[:500]}")
            lines.append("")
    lines.extend(
        [
            "## Compliance checklist (mandatory documents)",
            "",
        ]
    )
    for item in analysis.get("submission_checklist") or []:
        if isinstance(item, dict):
            lines.append(f"- [ ] {item.get('document', '—')} — {item.get('status', '')}")
    lines.extend(["", "## Governance snapshot", ""])
    gov = analysis.get("governance_report")
    if isinstance(gov, dict):
        lines.append(str(gov.get("audit_notes", "")))
        lines.append(f"- Retention (recommended years): {gov.get('retention_recommendation_years', '—')}")

    p_md = rd / "submission_pack_outline.md"
    p_md.write_text("\n".join(lines), encoding="utf-8")
    out["submission_pack_outline"] = str(p_md.relative_to(project_root))

    pdf_path = rd / "submission_pack.pdf"
    if _try_write_pdf(pdf_path, tender_id, analysis):
        out["submission_pack_pdf"] = str(pdf_path.relative_to(project_root))

    return out


def _try_write_pdf(path: Path, tender_id: str, analysis: dict[str, Any]) -> bool:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError:
        return False
    try:
        c = canvas.Canvas(str(path), pagesize=A4)
        w, h = A4
        y = h - 50
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, y, "Tender submission pack (summary)")
        y -= 28
        c.setFont("Helvetica", 10)
        for label, val in [
            ("Tender ID", tender_id),
            ("Tender No", str(analysis.get("tender_no", "—"))),
            ("Title", str(analysis.get("tender_name", "—"))[:80]),
            ("Verdict", str(analysis.get("verdict", "—"))),
            ("Confidence", str(analysis.get("confidence_score", "—"))),
        ]:
            c.drawString(50, y, f"{label}: {val}")
            y -= 14
            if y < 80:
                c.showPage()
                y = h - 50
        c.save()
        return True
    except Exception:
        return False
