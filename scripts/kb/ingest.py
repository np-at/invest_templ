"""Ingest: the primary write path. Agent writes a candidate-claim JSON file;
this validates it atomically (whole batch rejected or accepted), resolves
dependency aliases/triples to content-addressed IDs, dedups, and appends
events. See templates/candidate_claim.json for the contract.

Validation returns FIELD-LEVEL errors naming the field, the bad value, and
the allowed set (B4) — so a driving agent can self-correct in one turn.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import events as E
from . import projection as P

# Common near-miss spellings mapped to the canonical enum value.
_ALIASES = {
    "analytic_confidence": {"moderate": "mod", "medium": "mod", "m": "mod",
                            "hi": "high", "lo": "low"},
    "likelihood_band": {"even": "roughly_even", "50/50": "roughly_even",
                        "certain": "almost_certain", "impossible": "almost_no_chance"},
}


def _enum_error(field, value, allowed):
    hint = ""
    canon = _ALIASES.get(field, {}).get(str(value).strip().lower())
    if canon:
        hint = f" (did you mean {canon!r}?)"
    return f"{field}: got {value!r}, allowed {allowed}{hint}"


def _coerce_enum(field, value):
    """Accept near-miss spellings for enums, else return value unchanged."""
    return _ALIASES.get(field, {}).get(str(value).strip().lower(), value)


def validate(batch: dict, topic: str) -> list[str]:
    """Return a list of human-readable, field-level errors ([] == valid)."""
    errors: list[str] = []
    if not isinstance(batch, dict):
        return ["top level must be a JSON object with a 'claims' array"]

    source_aliases = {}
    for i, s in enumerate(batch.get("sources", [])):
        loc = f"sources[{i}]"
        if not s.get("uri"):
            errors.append(f"{loc}.uri: required")
        if "authority" in s and not isinstance(s["authority"], (int, float)):
            errors.append(f"{loc}.authority: must be a number 0..1")
        if s.get("alias"):
            source_aliases[s["alias"]] = s.get("uri")

    claims = batch.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("claims: required non-empty array")
        return errors

    claim_aliases = set()
    for i, c in enumerate(claims):
        loc = f"claims[{i}]"
        a = c.get("assertion")
        if not isinstance(a, dict) or not all(a.get(k) for k in ("s", "p", "o")):
            errors.append(f"{loc}.assertion: required object with non-empty s, p, o")
        if not c.get("warrant"):
            errors.append(f"{loc}.warrant: required (why the grounds support the claim)")
        if not c.get("rebuttal_conditions"):
            errors.append(f"{loc}.rebuttal_conditions: required non-empty array "
                          "(a claim with no way to be disproven is not admissible)")
        for field, allowed in (("epistemic_status", E.STATUSES),
                               ("likelihood_band", E.LIKELIHOOD_BANDS),
                               ("analytic_confidence", E.ANALYTIC_CONFIDENCE),
                               ("confidence_method", E.CONFIDENCE_METHODS)):
            if field in c and c[field] is not None:
                val = _coerce_enum(field, c[field])
                if val not in allowed:
                    errors.append(_enum_error(field, c[field], allowed))
        cs = c.get("confidence_score")
        if cs is not None and not (isinstance(cs, (int, float)) and 0 <= cs <= 1):
            errors.append(f"{loc}.confidence_score: must be a number in 0..1, got {cs!r}")
        for tf in ("t_valid", "t_invalid"):
            if c.get(tf):
                try:
                    E.to_utc_iso(c[tf])
                except ValueError:
                    errors.append(f"{loc}.{tf}: not a valid ISO-8601 timestamp, got {c[tf]!r} "
                                  "(use e.g. 2015-01-01T00:00:00+00:00 or ...Z)")
        for ev_i, ev in enumerate(c.get("evidence", [])):
            src = ev.get("source")
            if not src:
                errors.append(f"{loc}.evidence[{ev_i}].source: required (alias, src_ id, or uri)")
            elif str(src).startswith("@") and src not in source_aliases:
                errors.append(f"{loc}.evidence[{ev_i}].source: unresolved alias {src!r}")
        if c.get("alias"):
            claim_aliases.add(c["alias"])

    # depends_on references must resolve
    for i, c in enumerate(claims):
        for j, dep in enumerate(c.get("depends_on", [])):
            if isinstance(dep, str) and dep.startswith("@"):
                if dep not in claim_aliases:
                    errors.append(f"claims[{i}].depends_on[{j}]: unresolved alias {dep!r}")
            elif isinstance(dep, str) and dep.startswith("clm_"):
                pass
            elif isinstance(dep, dict) and all(dep.get(k) for k in ("s", "p", "o")):
                pass
            else:
                errors.append(f"claims[{i}].depends_on[{j}]: must be @alias, clm_ id, or "
                              f"{{s,p,o}} triple; got {dep!r}")
    return errors


def _resolve_source(ref, source_aliases):
    if str(ref).startswith("@"):
        uri = source_aliases[ref]
    elif str(ref).startswith("src_"):
        return ref
    else:
        uri = ref
    return E.source_id(uri)


def _resolve_dep(dep, topic, alias_to_id):
    if isinstance(dep, str) and dep.startswith("@"):
        return alias_to_id[dep]
    if isinstance(dep, str) and dep.startswith("clm_"):
        return dep
    if isinstance(dep, dict):
        return E.claim_id(topic, dep["s"], dep["p"], dep["o"])
    raise ValueError(dep)


def ingest_file(path: str | Path, *, topic: str | None = None) -> dict:
    batch = json.loads(Path(path).read_text())
    return ingest_batch(batch, topic=topic)


def ingest_batch(batch: dict, *, topic: str | None = None) -> dict:
    topic = E.active_topic(topic)
    errors = validate(batch, topic)
    if errors:
        return {"ok": False, "errors": errors}

    # existing state for dedup
    con = P.build_index(topic)
    existing_claims = {r["claim_id"] for r in con.execute("SELECT claim_id FROM claims")}
    existing_ev = {r["evidence_id"] for r in con.execute("SELECT evidence_id FROM evidence")}
    con.close()

    run = batch.get("agent_run")
    run_id = None
    if run:
        run_id = run.get("run_id") or ("run_" + E.now_iso())
        E.append_event("agent_run_started", {
            "run_id": run_id, "role": run.get("role"),
            "model": run.get("model"), "task": run.get("task"),
        }, topic=topic, agent_run=run_id)

    source_aliases = {s["alias"]: s["uri"] for s in batch.get("sources", []) if s.get("alias")}
    n_sources = 0
    for s in batch.get("sources", []):
        sid = E.source_id(s["uri"])
        E.append_event("source_registered", {
            "source_id": sid, "uri": s["uri"], "title": s.get("title"),
            "origin": s.get("origin"), "authority": s.get("authority"),
            "credibility": s.get("credibility"), "retrieved_at": s.get("retrieved_at"),
        }, topic=topic, agent_run=run_id)
        n_sources += 1

    # map claim aliases -> ids first (so depends_on can resolve)
    alias_to_id = {}
    for c in batch["claims"]:
        a = c["assertion"]
        cid = E.claim_id(topic, a["s"], a["p"], a["o"])
        if c.get("alias"):
            alias_to_id[c["alias"]] = cid

    created, skipped, ev_added = [], [], 0
    for c in batch["claims"]:
        a = c["assertion"]
        cid = E.claim_id(topic, a["s"], a["p"], a["o"])
        deps = [_resolve_dep(d, topic, alias_to_id) for d in c.get("depends_on", [])]
        if cid in existing_claims:
            skipped.append(cid)
        else:
            E.append_event("claim_created", {
                "claim_id": cid, "assertion": a,
                "warrant": c.get("warrant"), "backing": c.get("backing", []),
                "rebuttal_conditions": c.get("rebuttal_conditions", []),
                "grounds": c.get("grounds", []),
                "epistemic_status": _coerce_enum("epistemic_status",
                                                 c.get("epistemic_status", "hypothesized")),
                "likelihood_band": _coerce_enum("likelihood_band", c.get("likelihood_band")),
                "analytic_confidence": _coerce_enum("analytic_confidence", c.get("analytic_confidence")),
                "confidence_score": c.get("confidence_score"),
                "confidence_method": _coerce_enum("confidence_method", c.get("confidence_method")),
                "method": c.get("method"),
                "t_valid": E.to_utc_iso(c.get("t_valid")),
                "t_invalid": E.to_utc_iso(c.get("t_invalid")),
                "depends_on": deps,
            }, topic=topic, agent_run=run_id)
            created.append(cid)
        for ev in c.get("evidence", []):
            sid = _resolve_source(ev["source"], source_aliases)
            eid = E.evidence_id(cid, sid, ev.get("locator", ""))
            if eid in existing_ev:
                continue
            E.append_event("evidence_added", {
                "evidence_id": eid, "claim_id": cid, "source_id": sid,
                "quote": ev.get("quote"), "locator": ev.get("locator"),
            }, topic=topic, agent_run=run_id)
            existing_ev.add(eid)
            ev_added += 1

    # refresh index
    P.build_index(topic).close()
    return {"ok": True, "topic": topic, "sources": n_sources,
            "created": created, "skipped": skipped, "evidence_added": ev_added,
            "run_id": run_id}
