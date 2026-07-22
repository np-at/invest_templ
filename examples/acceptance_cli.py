#!/usr/bin/env python3
"""End-to-end acceptance test driven entirely through the `kb.py` CLI — the
surface an agent actually touches (the seed example drives the Python modules
directly). Reproduces the full lifecycle on a throwaway topic and cleans up.

    python examples/acceptance_cli.py

Covers: ingest from file -> conflict detect (pre-link lint FAILS) -> conflict
link (lint passes) -> promote -> source discredit + cascade -> rebuttal ->
both temporal axes -> world-time offset normalization (Z and +02:00 inputs).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kb import events as E  # noqa: E402  (for content-addressed ids only)

TOPIC = "_acceptance"
KB = [sys.executable, str(ROOT / "scripts" / "kb.py")]
_fail = 0


def check(name, cond):
    global _fail
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fail += 1


def run(*args):
    """Run kb.py <args> --topic TOPIC; return (rc, stdout)."""
    r = subprocess.run(KB + list(args) + ["--topic", TOPIC],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


BATCH = {
    "agent_run": {"run_id": "run_acc", "role": "investigator"},
    "sources": [
        {"alias": "@reg", "uri": "https://reg.example/orbis", "authority": 0.95},
        {"alias": "@wire", "uri": "https://wire.example/orbis", "authority": 0.8},
        {"alias": "@arch", "uri": "https://archive.example/orbis", "authority": 0.9},
        {"alias": "@blog", "uri": "https://blog.example/orbis", "authority": 0.3},
    ],
    "claims": [
        {"alias": "@hq", "assertion": {"s": "Orbis Ltd", "p": "headquartered_in", "o": "Berlin"},
         "warrant": "registry", "rebuttal_conditions": ["a later filing shows another city"],
         "grounds": ["reg lists Berlin"], "likelihood_band": "very_likely", "analytic_confidence": "high",
         "confidence_score": 0.85, "confidence_method": "source_corroboration",
         "evidence": [{"source": "@reg", "quote": "office: Berlin", "locator": "r1"},
                      {"source": "@wire", "quote": "Berlin", "locator": "p1"}]},
        {"alias": "@hq2", "assertion": {"s": "Orbis Ltd", "p": "headquartered_in", "o": "Munich"},
         "warrant": "blog", "rebuttal_conditions": ["the registry city differs"],
         "grounds": ["blog says Munich"], "confidence_score": 0.4, "confidence_method": "self_consistency",
         "evidence": [{"source": "@blog", "quote": "Munich", "locator": "p3"}]},
        {"alias": "@ceo", "assertion": {"s": "Orbis Ltd", "p": "ceo_is", "o": "Bob Lee"},
         "warrant": "archive", "rebuttal_conditions": ["another CEO documented for the period"],
         "grounds": ["archive tenure record"], "likelihood_band": "very_likely",
         "analytic_confidence": "high", "confidence_score": 0.8, "confidence_method": "source_corroboration",
         # deliberately mixed offset formats to exercise UTC normalization:
         "t_valid": "2016-01-01T00:00:00Z", "t_invalid": "2021-01-01T02:00:00+02:00",
         "evidence": [{"source": "@wire", "quote": "CEO Bob Lee", "locator": "p2"},
                      {"source": "@arch", "quote": "CEO 2016-2021: Bob Lee", "locator": "r7"}]},
    ],
}


def main():
    print("== CLI acceptance test ==")
    # clean any prior run
    log = E.topic_path(TOPIC)
    if log.exists():
        log.unlink()

    hq = E.claim_id(TOPIC, "Orbis Ltd", "headquartered_in", "Berlin")
    hq2 = E.claim_id(TOPIC, "Orbis Ltd", "headquartered_in", "Munich")
    ceo = E.claim_id(TOPIC, "Orbis Ltd", "ceo_is", "Bob Lee")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(BATCH, fh)
        batch_path = fh.name

    rc, out = run("ingest", batch_path)
    check("ingest via CLI accepted 3 claims", rc == 0 and "3 new claim" in out)

    rc, out = run("conflict", "detect")
    check("conflict detect flags Berlin/Munich as CONFLICT/UNLINKED",
          "CONFLICT" in out and "UNLINKED" in out)

    rc, out = run("lint")
    check("lint FAILS before linking (silent-resolution error)",
          rc == 1 and "silent-resolution" in out)

    rc, out = run("conflict", "link", hq, hq2, "--type", "factual")
    check("conflict link succeeds", rc == 0)

    rc, out = run("lint")
    check("lint PASSES after linking", rc == 0)

    rc, out = run("claim", "promote", ceo, "--to", "corroborated")
    check("CEO promoted to corroborated (2 authoritative sources)", rc == 0)

    # world-time axis, exercising normalized offsets
    rc, out = run("query", "as-of", "2019-06-01T00:00:00+00:00", "--world")
    check("as-of(world, 2019) shows Bob Lee was CEO", "Bob Lee" in out)
    rc, out = run("query", "as-of", "2022-06-01T00:00:00+00:00", "--world")
    check("as-of(world, 2022) excludes him (past normalized t_invalid)", "Bob Lee" not in out)

    # verify the +02:00 input was normalized to UTC on disk
    rc, out = run("claim", "show", ceo)
    shown = json.loads(out)
    check("t_invalid normalized to UTC (+00:00, 00:00:00)",
          shown["t_invalid"].startswith("2021-01-01T00:00:00") and shown["t_invalid"].endswith("+00:00"))

    # snapshot for system-time proof (HQ still hypothesized-or-disputed but present)
    rc, before = run("query", "as-of", "2999-01-01T00:00:00+00:00")

    # invalidation + cascade via CLI
    rc, out = run("source", "discredit", "https://reg.example/orbis", "--reason", "tampering")
    check("source discredit succeeds", rc == 0)
    rc, out = run("claim", "show", hq)
    check("HQ (cites discredited registry) flagged needs_review",
          json.loads(out)["epistemic_status"] == "needs_review")
    rc, out = run("claim", "show", ceo)
    check("CEO (independent sources) NOT flagged by discredit",
          json.loads(out)["epistemic_status"] == "corroborated")

    # rebuttal CLI path (the falsifiability mechanism)
    rc, out = run("claim", "rebuttal", hq2, "--condition", "registry confirms Berlin",
                  "--evidence", "reg r1")
    check("claim rebuttal moves target to needs_review", rc == 0
          and json.loads(run("claim", "show", hq2)[1])["epistemic_status"] == "needs_review")

    rc, out = run("lint")
    check("final lint has 0 errors", rc == 0)
    rc, out = run("review-queue")
    check("review-queue non-empty", "needs_review" in out or "trigger" in out or out.strip() != "Review queue empty.")

    # reindex reproduces
    run("reindex")
    rc, after = run("query", "as-of", "2999-01-01T00:00:00+00:00")
    check("reindex reproduces identical system-time belief state", before == after)

    Path(batch_path).unlink(missing_ok=True)
    if log.exists():
        log.unlink()
    if E.INDEX_PATH.exists():
        E.INDEX_PATH.unlink()

    print(f"\n== {'ALL CLI CHECKS PASSED' if _fail == 0 else str(_fail) + ' CHECK(S) FAILED'} ==")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
