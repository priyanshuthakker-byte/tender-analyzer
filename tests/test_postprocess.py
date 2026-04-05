from app.postprocess import compute_confidence_score, enrich_analysis


def test_enrich_adds_symbols_and_confidence():
    merged = {
        "verdict": "BID",
        "pq_criteria": [
            {
                "sl_no": "1",
                "status_color": "GREEN",
                "audit_status": "Met",
                "company_status": "Met",
            },
            {
                "sl_no": "2",
                "status_color": "RED",
                "audit_status": "Critical",
                "company_status": "Not Met",
            },
        ],
        "tq_criteria": [],
        "prebid_queries": [{"clause": "1.1", "query": "test?"}],
        "overall_verdict": {"verdict": "BID"},
    }
    out = enrich_analysis(merged)
    assert out["pq_criteria"][0]["status_symbol"] == "✔"
    assert out["pq_criteria"][1]["status_symbol"] == "✘"
    assert "confidence_score" in out
    assert out["prebid_queries"][0]["status"] == "drafted"
    assert "✔" in out["overall_verdict"]["verdict_display"]


def test_confidence_no_bid_cap():
    merged = {
        "verdict": "NO-BID",
        "pq_criteria": [{"status_color": "GREEN"}],
        "tq_criteria": [],
    }
    score, _ = compute_confidence_score(merged)
    assert score <= 35
