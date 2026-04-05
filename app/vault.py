"""Document vault: filename hints + optional manifest with certification expiry."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any


def load_vault_manifest(vault_path: Path | None) -> list[dict[str, Any]]:
    """
    Read vault_manifest.json from the vault folder if present.
    See document_vault/vault_manifest.example.json for schema.
    """
    if not vault_path or not vault_path.is_dir():
        return []
    mf = vault_path / "vault_manifest.json"
    if not mf.is_file():
        return []
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        docs = data.get("documents") or []
        return [d for d in docs if isinstance(d, dict)]
    except (OSError, json.JSONDecodeError):
        return []


def _expiry_status(valid_until: str | None) -> tuple[str, str | None]:
    """Return (status, human note). status: unknown | no_expiry | valid | expiring_soon | expired."""
    if not valid_until or str(valid_until).strip().lower() in ("null", "—", "-", ""):
        return "no_expiry_on_record", None
    raw = str(valid_until).strip()[:10]
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            y, m, d = int(raw[:4]), int(raw[5:7]), int(raw[8:10])
            exp = date(y, m, d)
        else:
            return "unknown_format", valid_until
    except ValueError:
        return "unknown_format", valid_until

    today = date.today()
    days = (exp - today).days
    if days < 0:
        return "expired", f"Expired {raw}"
    if days <= 90:
        return "expiring_soon", f"Expires {raw} ({days} days)"
    return "valid", f"Valid until {raw}"


def scan_vault_hints(
    vault_path: Path | None,
    submission_checklist: list[dict[str, Any]],
    manifest_docs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Match checklist rows to vault files and manifest entries (tag + expiry).
    """
    if not vault_path or not vault_path.is_dir():
        return []

    manifest_docs = manifest_docs if manifest_docs is not None else load_vault_manifest(vault_path)
    tag_index: dict[str, dict[str, Any]] = {}
    for doc in manifest_docs:
        tag = str(doc.get("tag", "")).strip().upper()
        if tag:
            tag_index[tag] = doc

    files = [p for p in vault_path.iterdir() if p.is_file() and p.name != "vault_manifest.json"]
    hints: list[dict[str, Any]] = []

    for item in submission_checklist:
        if not isinstance(item, dict):
            continue
        doc = str(item.get("document", "") or "")
        if not doc:
            continue
        vault_tag = str(item.get("vault_tag", "") or "").strip().upper()
        tokens = [t.lower() for t in re.split(r"[^\w]+", doc) if len(t) > 2][:8]

        matches = []
        for f in files:
            name = f.name.lower()
            hit = sum(1 for t in tokens if t in name)
            if hit >= 1:
                matches.append({"file": f.name, "score": hit})
        matches.sort(key=lambda x: -x["score"])

        manifest_hit: dict[str, Any] | None = None
        expiry_info: dict[str, Any] = {}
        if vault_tag and vault_tag in tag_index:
            m = tag_index[vault_tag]
            manifest_hit = {
                "tag": vault_tag,
                "expected_file": m.get("file"),
                "valid_until": m.get("valid_until"),
            }
            st, note = _expiry_status(m.get("valid_until"))
            expiry_info = {"status": st, "note": note}
        else:
            # Try loose match: any manifest file name appears in checklist text
            for m in manifest_docs:
                fn = str(m.get("file", "")).lower()
                if fn and fn in doc.lower():
                    st, note = _expiry_status(m.get("valid_until"))
                    manifest_hit = {
                        "tag": str(m.get("tag", "")),
                        "expected_file": m.get("file"),
                        "valid_until": m.get("valid_until"),
                    }
                    expiry_info = {"status": st, "note": note}
                    break

        hints.append(
            {
                "checklist_document": doc,
                "vault_tag": vault_tag or None,
                "vault_matches": matches[:5],
                "manifest": manifest_hit,
                "cert_expiry": expiry_info,
                "note": "Maintain vault_manifest.json for audit-ready expiry tracking; filenames must exist in vault.",
            }
        )
    return hints
