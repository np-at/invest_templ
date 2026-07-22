#!/usr/bin/env python3
"""Seed example — a tiny worked investigation that doubles as the end-to-end
smoke test. Run it:

    python examples/seed_example.py

It builds topic `demo`, then asserts the whole belief-revision lifecycle:
conflict detection + linking, source discredit -> dependency cascade, and
BOTH temporal axes (system-time "what did we believe on D" and world-time
"what do we now believe was true on D"). Exits non-zero if any check fails.

`python scripts/fresh_start.py --yes` wipes everything this creates (and the
fresh_start script itself) to convert the repo to a real investigation.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from kb import claims as C          # noqa: E402
from kb import conflicts as CF      # noqa: E402
from kb import events as E          # noqa: E402
from kb import ingest as ING        # noqa: E402
from kb import invalidation as INV  # noqa: E402
from kb import lint as L            # noqa: E402
from kb import projection as P      # noqa: E402
from kb import query as Q           # noqa: E402
from kb import report as R          # noqa: E402

TOPIC = "demo"
_fail = 0


def check(name, cond):
    global _fail
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fail += 1


def reset():
    E.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    log = E.topic_path(TOPIC)
    if log.exists():
        log.unlink()
    if E.INDEX_PATH.exists():
        E.INDEX_PATH.unlink()
    log.touch()
    cfg = E.read_config()
    cfg["topic"] = TOPIC
    E.write_config(cfg)


BATCH = {
    "agent_run": {"run_id": "run_seed", "role": "investigator", "model": "seed", "task": "demo"},
    "sources": [
        {"alias": "@gov", "uri": "https://gov.example/registry/nexa", "origin": "National Registry",
         "authority": 0.95, "credibility": "primary"},
        {"alias": "@wire", "uri": "https://wire.example/nexa", "origin": "Wire Agency", "authority": 0.8},
        {"alias": "@blog", "uri": "https://blog.example/nexa-munich", "origin": "Anon Blog", "authority": 0.3},
        {"alias": "@archive", "uri": "https://archive.example/nexa-leadership", "origin": "Archive",
         "authority": 0.9},
    ],
    "claims": [
        {"alias": "@hq",
         "assertion": {"s": "Nexa Corp", "p": "headquartered_in", "o": "Berlin",
                       "nl": "Nexa Corp is headquartered in Berlin."},
         "grounds": ["Registry lists registered office in Berlin"],
         "warrant": "The national company registry is authoritative for registered office.",
         "backing": ["Statutory registry record"],
         "rebuttal_conditions": ["A later registry filing shows a different registered office."],
         "likelihood_band": "very_likely", "analytic_confidence": "high",
         "confidence_score": 0.85, "confidence_method": "source_corroboration",
         "evidence": [{"source": "@gov", "quote": "Registered office: Berlin", "locator": "rec.1"},
                      {"source": "@wire", "quote": "Berlin-based Nexa Corp", "locator": "para.1"}]},

        {"alias": "@tax",
         "assertion": {"s": "Nexa Corp", "p": "pays_corporate_tax_in", "o": "Germany",
                       "nl": "Nexa Corp pays corporate tax in Germany."},
         "grounds": ["A Berlin HQ implies German tax residency"],
         "warrant": "A company is taxed where it is headquartered.",
         "backing": ["General corporate tax residency principle"],
         "rebuttal_conditions": ["Evidence of tax residency elsewhere is found."],
         "likelihood_band": "likely", "analytic_confidence": "mod",
         "confidence_score": 0.7, "confidence_method": "self_consistency",
         "depends_on": ["@hq"],
         "evidence": [{"source": "@gov", "quote": "Tax ID: DE...", "locator": "rec.4"}]},

        {"alias": "@hq_munich",
         "assertion": {"s": "Nexa Corp", "p": "headquartered_in", "o": "Munich",
                       "nl": "Nexa Corp is headquartered in Munich."},
         "grounds": ["Blog post claims a Munich head office"],
         "warrant": "Reported location.",
         "backing": ["Secondary reporting"],
         "rebuttal_conditions": ["The registry office differs from the reported city."],
         "likelihood_band": "unlikely", "analytic_confidence": "low",
         "confidence_score": 0.4, "confidence_method": "self_consistency",
         "evidence": [{"source": "@blog", "quote": "Nexa's Munich HQ", "locator": "para.3"}]},

        {"alias": "@ceo",
         "assertion": {"s": "Nexa Corp", "p": "ceo_is", "o": "Alice Stone",
                       "nl": "Alice Stone was CEO of Nexa Corp (2015-2020)."},
         "grounds": ["Leadership archive lists Alice Stone as CEO 2015-2020"],
         "warrant": "Archived leadership records document tenure.",
         "backing": ["Archive cross-checked with wire reports"],
         "rebuttal_conditions": ["A record shows a different CEO during 2015-2020."],
         "likelihood_band": "very_likely", "analytic_confidence": "high",
         "confidence_score": 0.8, "confidence_method": "source_corroboration",
         "t_valid": "2015-01-01T00:00:00+00:00", "t_invalid": "2020-01-01T00:00:00+00:00",
         "evidence": [{"source": "@archive", "quote": "CEO 2015-2020: Alice Stone", "locator": "row.7"},
                      {"source": "@wire", "quote": "Nexa CEO Alice Stone", "locator": "para.2"}]},
    ],
}


def main():
    print("== Seeding demo investigation ==")
    reset()
    E.append_event("seed_marker", {"note": "Created by examples/seed_example.py"},
                   topic=TOPIC, agent_run="run_seed")

    res = ING.ingest_batch(BATCH, topic=TOPIC)
    check("ingest accepted", res["ok"] and len(res["created"]) == 4)

    hq = E.claim_id(TOPIC, "Nexa Corp", "headquartered_in", "Berlin")
    tax = E.claim_id(TOPIC, "Nexa Corp", "pays_corporate_tax_in", "Germany")
    munich = E.claim_id(TOPIC, "Nexa Corp", "headquartered_in", "Munich")
    ceo = E.claim_id(TOPIC, "Nexa Corp", "ceo_is", "Alice Stone")

    # Promote on evidence (curator decision).
    C.change_status(hq, "corroborated", reason="2 authoritative sources", agent_run="run_curator", topic=TOPIC)
    C.change_status(tax, "probable", reason="derived, single source", agent_run="run_curator", topic=TOPIC)
    C.change_status(ceo, "corroborated", reason="archive+wire", agent_run="run_curator", topic=TOPIC)

    t0 = E.now_iso()   # snapshot: HQ believed, before any conflict/discredit

    # Conflict: Berlin vs Munich (functional predicate) -> detect + link (retain both).
    pairs = CF.detect(topic=TOPIC)
    hq_conflict = [p for p in pairs if p["functional"]
                   and {p["claim_a"], p["claim_b"]} == {hq, munich}]
    check("structured conflict detected (Berlin vs Munich)", len(hq_conflict) == 1)
    CF.link(hq, munich, conflict_type="factual", note="registry vs blog",
            agent_run="run_reconciler", topic=TOPIC)
    check("both sides moved to disputed on link",
          C.get_claim(hq, TOPIC)["epistemic_status"] == "disputed"
          and C.get_claim(munich, TOPIC)["epistemic_status"] == "disputed")

    # Invalidation: the registry source is discredited -> cascade re-flags dependents.
    out = INV.discredit_source("https://gov.example/registry/nexa", reason="registry data breach / tampering",
                               agent_run="run_curator", topic=TOPIC)
    check("HQ flagged needs_review (cites discredited source)",
          C.get_claim(hq, TOPIC)["epistemic_status"] == "needs_review")
    check("dependent tax claim cascaded to needs_review",
          C.get_claim(tax, TOPIC)["epistemic_status"] == "needs_review")

    # System-time axis: what did we BELIEVE at t0? (history preserved)
    believed_t0 = {r["claim_id"] for r in Q.as_of_system(t0, topic=TOPIC)}
    check("as-of(system, t0) STILL shows HQ believed (history preserved)", hq in believed_t0)
    believed_now = {r["claim_id"] for r in Q.as_of_system(E.now_iso(), topic=TOPIC)}
    check("as-of(system, now) shows HQ no longer believed (belief revised)", hq not in believed_now)

    # World-time axis: what do we now believe was TRUE on a date?
    w2017 = {r["claim_id"] for r in Q.as_of_world("2017-06-01T00:00:00+00:00", topic=TOPIC)}
    w2022 = {r["claim_id"] for r in Q.as_of_world("2022-06-01T00:00:00+00:00", topic=TOPIC)}
    check("as-of(world, 2017) shows Alice Stone was CEO", ceo in w2017)
    check("as-of(world, 2022) shows she was not (outside t_valid..t_invalid)", ceo not in w2022)

    # Guardrails clean.
    lintres = L.lint(topic=TOPIC)
    check("lint: 0 errors", len(lintres["errors"]) == 0)
    check("review-queue populated (needs_review + conflict)", len(L.review_queue(topic=TOPIC)) > 0)

    # Canonical-store proof: rebuild the index from events, identical result.
    P.build_index(TOPIC).close()
    rebuilt = {r["claim_id"] for r in Q.as_of_system(E.now_iso(), topic=TOPIC)}
    check("reindex reproduces identical belief set", rebuilt == believed_now)

    print("\n" + R.report(topic=TOPIC))
    print(f"== {'ALL CHECKS PASSED' if _fail == 0 else str(_fail) + ' CHECK(S) FAILED'} ==")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
