"""Normalize AI output: status symbols, confidence score, pre-bid defaults."""

from __future__ import annotations

import copy
from typing import Any


def _symbol_for_row(row: dict[str, Any]) -> str:
    """Map enterprise statuses to ✔ / ✘ / ⚠ for UI and reports."""
    color = str(row.get("status_color", "")).upper()
    audit = str(row.get("audit_status", "")).lower()
    company = str(row.get("company_status", "")).lower()

    if color == "RED" or "critical" in audit or "not met" in company:
        return "✘"
    if color == "AMBER" or "pending" in audit or "conditional" in company:
        return "⚠"
    if color == "GREEN" or "met" in audit:
        return "✔"
    return "⚠"


def _count_pq_tq(merged: dict[str, Any]) -> tuple[int, int, int]:
    g = a = r = 0
    for key in ("pq_criteria", "tq_criteria"):
        for row in merged.get(key) or []:
            if not isinstance(row, dict):
                continue
            c = str(row.get("status_color", "")).upper()
            if c == "GREEN":
                g += 1
            elif c == "RED":
                r += 1
            elif c == "AMBER":
                a += 1
            else:
                a += 1
    return g, a, r


def compute_confidence_score(merged: dict[str, Any]) -> tuple[int, str]:
    """
    Heuristic 0–100 score from PQ/TQ colour counts + verdict (not a trained model).
    Documented as rule-based; replace with ML later if needed.
    """
    g, amb, red = _count_pq_tq(merged)
    total = g + amb + red
    verdict = str(merged.get("verdict", "CONDITIONAL")).upper()

    if total == 0:
        base = 55
        reason = "No PQ/TQ rows parsed; score reflects uncertainty."
    else:
        base = int(round(100 * (g + 0.5 * amb) / max(total, 1)))
        reason = f"Based on {g} met, {amb} pending/conditional, {red} critical gaps (PQ+TQ)."

    if "NO" in verdict and "BID" in verdict:
        base = min(base, 35)
        reason += " Verdict NO-BID caps score."
    elif verdict == "BID":
        base = max(base, 60)
        reason += " Verdict BID supports higher confidence."

    return max(0, min(100, base)), reason


def compute_pack_validation(merged: dict[str, Any]) -> dict[str, Any]:
    """
    Lightweight validation vs checklist + PQ/TQ + open pre-bid items.
    Links logically to submission pack exports (same tender_id folder).
    """
    checklist = merged.get("submission_checklist") or []
    critical = prepare = 0
    for item in checklist:
        if not isinstance(item, dict):
            continue
        st = str(item.get("status", "")).upper()
        if "CRITICAL" in st:
            critical += 1
        if "PREPARE" in st or "COMPILE" in st:
            prepare += 1

    pq_red = tq_red = pq_pending = 0
    for row in merged.get("pq_criteria") or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("status_color", "")).upper()
        sym = str(row.get("status_symbol", ""))
        if c == "RED" or sym == "✘":
            pq_red += 1
        elif c == "AMBER" or sym == "⚠":
            pq_pending += 1
    for row in merged.get("tq_criteria") or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("status_color", "")).upper()
        if c == "RED":
            tq_red += 1

    prebid_open = 0
    for q in merged.get("prebid_queries") or []:
        if isinstance(q, dict) and str(q.get("status", "drafted")).lower() == "drafted":
            prebid_open += 1

    exports = merged.get("export_paths") or {}
    linked = list(exports.keys()) if isinstance(exports, dict) else []

    issues: list[str] = []
    if critical:
        issues.append(f"{critical} checklist item(s) marked CRITICAL.")
    if pq_red or tq_red:
        issues.append(f"{pq_red} PQ and {tq_red} TQ row(s) flagged red.")
    if prebid_open:
        issues.append(f"{prebid_open} pre-bid query(ies) still in drafted status.")
    if not linked:
        issues.append("Submission pack files not yet on disk (run successful analyse).")

    readiness = 100
    readiness -= min(40, critical * 15)
    readiness -= min(30, (pq_red + tq_red) * 5)
    readiness -= min(20, prebid_open * 4)
    readiness = max(0, min(100, readiness))

    return {
        "readiness_score": readiness,
        "checklist_critical_count": critical,
        "checklist_prepare_count": prepare,
        "pq_red_count": pq_red,
        "tq_red_count": tq_red,
        "pq_pending_count": pq_pending,
        "prebid_drafted_count": prebid_open,
        "submission_pack_exports": linked,
        "issues": issues,
        "validated_against": ["submission_checklist", "pq_criteria", "tq_criteria", "prebid_queries"],
    }


def enrich_analysis(merged: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with symbols, confidence, and pre-bid status defaults."""
    out = copy.deepcopy(merged)

    for key in ("pq_criteria", "tq_criteria"):
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                row["status_symbol"] = _symbol_for_row(row)

    queries = out.get("prebid_queries")
    if isinstance(queries, list):
        for i, q in enumerate(queries):
            if isinstance(q, dict) and "status" not in q:
                q["status"] = "drafted"
                q["q_index"] = i

    score, reason = compute_confidence_score(out)
    out["confidence_score"] = score
    out["confidence_basis"] = reason

    ov = out.get("overall_verdict")
    if isinstance(ov, dict):
        ov.setdefault("verdict", out.get("verdict", "CONDITIONAL"))
        # Dashboard-friendly display verdict
        v = str(ov.get("verdict", "")).upper()
        if "NO" in v and "BID" in v:
            ov["verdict_display"] = "✘ No-Bid"
        elif v == "BID":
            ov["verdict_display"] = "✔ Bid"
        else:
            ov["verdict_display"] = "⚠ Conditional"

    out["pack_validation"] = compute_pack_validation(out)
    return out
