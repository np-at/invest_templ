"""Queries: bi-temporal as-of (both axes), source lineage, status filters,
revision history, single-claim detail.

Two temporal axes are kept distinct (report §E, review B2):
  - SYSTEM time: "what did we BELIEVE on date D" — replay events up to D.
    Answered by re-projecting with as_of=D (the discredit/retract events after
    D simply are not applied).
  - WORLD time: "what do we NOW believe was TRUE on date D" — read current
    state, filter by t_valid/t_invalid windows.
"""
from __future__ import annotations

import json

from . import events as E
from . import projection as P


def as_of_system(date_iso: str, topic: str | None = None) -> list[dict]:
    con = P.build_index(topic, as_of=date_iso, db_path=":memory:")
    rows = con.execute(
        "SELECT claim_id, subj, pred, obj, epistemic_status, confidence_score "
        "FROM claims WHERE epistemic_status IN ('probable','corroborated','confirmed') "
        "ORDER BY subj, pred").fetchall()
    con.close()
    return [dict(r) for r in rows]


def as_of_world(date_iso: str, topic: str | None = None) -> list[dict]:
    con = P.build_index(topic)
    rows = con.execute(
        "SELECT claim_id, subj, pred, obj, epistemic_status, t_valid, t_invalid "
        "FROM claims WHERE epistemic_status IN ('probable','corroborated','confirmed') "
        "AND (t_valid IS NULL OR t_valid <= ?) "
        "AND (t_invalid IS NULL OR t_invalid > ?) ORDER BY subj, pred",
        (date_iso, date_iso)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def downstream(source_uri_or_id: str, topic: str | None = None) -> dict:
    """Data-lineage: which claims rest (directly or transitively) on a source.
    Answers 'if this source is discredited, what is affected?' (report §A)."""
    sid = source_uri_or_id if str(source_uri_or_id).startswith("src_") else E.source_id(source_uri_or_id)
    con = P.build_index(topic)
    direct = [r["claim_id"] for r in con.execute(
        "SELECT DISTINCT claim_id FROM evidence WHERE source_id=?", (sid,))]
    visited, queue, transitive = set(direct), list(direct), []
    while queue:
        cur = queue.pop(0)
        for r in con.execute("SELECT from_id FROM edges WHERE kind='depends_on' AND to_id=?", (cur,)):
            if r["from_id"] not in visited:
                visited.add(r["from_id"])
                queue.append(r["from_id"])
                transitive.append(r["from_id"])
    con.close()
    return {"source_id": sid, "direct": direct, "transitive_dependents": transitive}


def by_status(status: str, topic: str | None = None) -> list[dict]:
    con = P.build_index(topic)
    rows = con.execute(
        "SELECT claim_id, subj, pred, obj, epistemic_status, confidence_score, review_reason "
        "FROM claims WHERE epistemic_status=? ORDER BY subj, pred", (status,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def revision_log(cid: str, topic: str | None = None) -> list[dict]:
    con = P.build_index(topic)
    rows = con.execute(
        "SELECT event, detail, t_created, agent_run FROM revisions WHERE claim_id=? ORDER BY seq",
        (cid,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        out.append(d)
    return out


def show(cid: str, topic: str | None = None) -> dict | None:
    con = P.build_index(topic)
    row = con.execute("SELECT * FROM claims WHERE claim_id=?", (cid,)).fetchone()
    if not row:
        con.close()
        return None
    claim = dict(row)
    for f in ("backing", "rebuttal", "depends_on", "grounds"):
        claim[f] = json.loads(claim[f]) if claim[f] else []
    claim["evidence"] = [dict(r) for r in con.execute(
        "SELECT e.evidence_id, e.source_id, e.quote, e.locator, s.uri, s.title, s.authority, s.discredited "
        "FROM evidence e LEFT JOIN sources s ON s.source_id=e.source_id WHERE e.claim_id=?", (cid,))]
    claim["conflicts_with"] = [
        (r["from_id"] if r["to_id"] == cid else r["to_id"]) for r in con.execute(
            "SELECT from_id, to_id FROM edges WHERE kind='conflicts_with' AND (from_id=? OR to_id=?)",
            (cid, cid))]
    claim["revisions"] = revision_log(cid, topic)
    con.close()
    return claim
