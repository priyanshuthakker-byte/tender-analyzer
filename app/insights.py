"""
Lightweight portfolio snapshot from stored analyses — not a knowledge graph.
Use for dashboards until you plug a real graph / BI tool.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def build_insights(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter()
    risk_cats = Counter()
    pq_red = pq_green = tq_red = 0
    prebid_drafted_total = 0

    for a in analyses:
        if not isinstance(a, dict):
            continue
        v = str(a.get("verdict", "UNKNOWN")).upper()
        if "NO" in v and "BID" in v:
            verdicts["NO-BID"] += 1
        elif v == "BID":
            verdicts["BID"] += 1
        else:
            verdicts["CONDITIONAL"] += 1

        for rh in a.get("risk_highlights") or []:
            if isinstance(rh, dict):
                cat = str(rh.get("category", "Other")).split("|")[0].strip()
                risk_cats[cat or "Other"] += 1

        for row in a.get("pq_criteria") or []:
            if isinstance(row, dict) and str(row.get("status_color", "")).upper() == "RED":
                pq_red += 1
            elif isinstance(row, dict) and str(row.get("status_color", "")).upper() == "GREEN":
                pq_green += 1

        for row in a.get("tq_criteria") or []:
            if isinstance(row, dict) and str(row.get("status_color", "")).upper() == "RED":
                tq_red += 1

        for q in a.get("prebid_queries") or []:
            if isinstance(q, dict) and str(q.get("status", "drafted")).lower() == "drafted":
                prebid_drafted_total += 1

    n = len(analyses)
    return {
        "tenders_analysed": n,
        "verdict_breakdown": dict(verdicts),
        "risk_categories_mentioned": dict(risk_cats.most_common(15)),
        "aggregate_pq_red_flags": pq_red,
        "aggregate_pq_met": pq_green,
        "aggregate_tq_red_flags": tq_red,
        "open_prebid_queries_count": prebid_drafted_total,
        "disclaimer": (
            "Rule-based aggregates from stored JSON only — not a trained learning engine or graph DB. "
            "Use for trends; verify against source tenders."
        ),
    }
