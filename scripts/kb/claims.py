"""Claim lifecycle: status transitions (legal-matrix enforced), promotion
threshold checks, confidence recalibration, manual source registration.

The promotion thresholds (report §Recommendations) are CHECKED here but the
decision to promote is the curator agent's — code refuses illegal transitions
and reports eligibility; it does not auto-promote.
"""
from __future__ import annotations

from . import events as E
from . import projection as P

AUTHORITY_THRESHOLD = 0.5   # a source counts as "authoritative" at/above this
PROMOTE_CONF = 0.7          # corroborated bar
CONFIRM_CONF = 0.9          # confirmed bar (NELL's own promotion bar)


def get_claim(cid: str, topic: str | None = None) -> dict | None:
    con = P.build_index(topic)
    row = con.execute("SELECT * FROM claims WHERE claim_id=?", (cid,)).fetchone()
    result = dict(row) if row else None
    con.close()
    return result


def change_status(cid: str, to: str, *, reason: str | None = None,
                  agent_run: str | None = None, topic: str | None = None) -> dict:
    if to not in E.STATUSES:
        raise SystemExit(f"Unknown status {to!r}; allowed {E.STATUSES}")
    claim = get_claim(cid, topic)
    if not claim:
        raise SystemExit(f"No such claim: {cid}")
    cur = claim["epistemic_status"]
    if to == cur:
        return {"ok": True, "noop": True, "status": cur}
    legal = E.TRANSITIONS.get(cur, set())
    if to not in legal:
        raise SystemExit(
            f"Illegal transition {cur!r} -> {to!r} for {cid}. Legal: {sorted(legal) or 'none (terminal)'}."
            + (" (retracted->hypothesized is revival)" if cur == "retracted" else ""))
    E.append_event("status_changed", {"claim_id": cid, "from": cur, "to": to, "reason": reason},
                   topic=topic, agent_run=agent_run)
    P.build_index(topic).close()
    return {"ok": True, "from": cur, "to": to}


def promotion_check(cid: str, topic: str | None = None) -> dict:
    """Report eligibility against the report's thresholds. Counts DISTINCT
    authoritative source_ids (independence is agent-asserted, not counted — S5).
    """
    con = P.build_index(topic)
    claim = con.execute("SELECT * FROM claims WHERE claim_id=?", (cid,)).fetchone()
    if not claim:
        con.close()
        raise SystemExit(f"No such claim: {cid}")
    rows = con.execute(
        "SELECT DISTINCT s.source_id, s.authority, s.discredited "
        "FROM evidence e JOIN sources s ON s.source_id=e.source_id WHERE e.claim_id=?", (cid,)
    ).fetchall()
    con.close()
    authoritative = [r for r in rows if (r["authority"] or 0) >= AUTHORITY_THRESHOLD and not r["discredited"]]
    n_auth = len(authoritative)
    conf = claim["confidence_score"] or 0.0
    import json
    rebuttals = json.loads(claim["rebuttal"] or "[]")
    open_rebuttals = len(rebuttals)  # presence of stated conditions; verifier resolves them
    eligible_corroborated = n_auth >= 2 and conf >= PROMOTE_CONF
    eligible_confirmed = conf >= CONFIRM_CONF and n_auth >= 2
    return {
        "claim_id": cid, "status": claim["epistemic_status"],
        "distinct_authoritative_sources": n_auth,
        "confidence_score": conf,
        "stated_rebuttal_conditions": open_rebuttals,
        "eligible_corroborated": eligible_corroborated,
        "eligible_confirmed": eligible_confirmed,
        "confidence_method": claim["confidence_method"],
    }


def recalibrate(cid: str, new_score: float, *, method: str,
                agent_run: str | None = None, topic: str | None = None) -> dict:
    if method not in E.CONFIDENCE_METHODS:
        raise SystemExit(f"Unknown confidence_method {method!r}; allowed {E.CONFIDENCE_METHODS}")
    if not (0 <= new_score <= 1):
        raise SystemExit("confidence_score must be in 0..1")
    old = get_claim(cid, topic)
    if not old:
        raise SystemExit(f"No such claim: {cid}")
    E.append_event("confidence_recalibrated", {
        "claim_id": cid, "old_score": old["confidence_score"],
        "new_score": new_score, "method": method,
    }, topic=topic, agent_run=agent_run)
    P.build_index(topic).close()
    return {"ok": True, "old": old["confidence_score"], "new": new_score, "method": method}


def trigger_rebuttal(cid: str, condition: str, *, evidence: str | None = None,
                     agent_run: str | None = None, topic: str | None = None) -> dict:
    """Record that a claim's stated rebuttal condition has fired (the
    falsifiability mechanism). Moves the claim to needs_review and logs the
    condition + evidence to its revision history."""
    if not get_claim(cid, topic):
        raise SystemExit(f"No such claim: {cid}")
    E.append_event("rebuttal_triggered", {"claim_id": cid, "condition": condition, "evidence": evidence},
                   topic=topic, agent_run=agent_run)
    P.build_index(topic).close()
    return {"ok": True, "claim_id": cid, "status": "needs_review", "condition": condition}


def add_source(uri: str, *, title: str | None = None, origin: str | None = None,
               authority: float | None = None, credibility: str | None = None,
               retrieved_at: str | None = None, agent_run: str | None = None,
               topic: str | None = None) -> dict:
    sid = E.source_id(uri)
    E.append_event("source_registered", {
        "source_id": sid, "uri": uri, "title": title, "origin": origin,
        "authority": authority, "credibility": credibility,
        "retrieved_at": retrieved_at or E.now_iso(),
    }, topic=topic, agent_run=agent_run)
    P.build_index(topic).close()
    return {"ok": True, "source_id": sid}
