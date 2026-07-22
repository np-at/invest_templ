#!/usr/bin/env python3
"""fresh_start.py — convert this template from the seeded demo into a clean,
real investigation, then delete the seed AND this script itself.

    python scripts/fresh_start.py --topic my-investigation --yes

Safeguards (this deletes files, so it is deliberately cautious):
  * Refuses to run on a REAL corpus. It proceeds only if the demo is all
    that's here: no event log other than `demo.jsonl`, and that log carries a
    `seed_marker` event. Any other topic => it stops and changes nothing.
  * Requires --yes. Without it, prints what it WOULD do and exits.
  * Deletes only known seed artifacts: kb/events/demo.jsonl, kb/index.db,
    examples/seed_example.py, and itself. Never `rm -rf kb/`.
  * Idempotent: if the seed is already gone it just (re)configures the topic.
  * Does NOT touch git. It leaves a dirty working tree for you to review and
    commit yourself.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from kb import events as E          # noqa: E402
from kb import projection as P      # noqa: E402

SELF = Path(__file__).resolve()
SEED_EXAMPLE = ROOT / "examples" / "seed_example.py"


def _demo_log_is_seed() -> tuple[bool, str]:
    logs = sorted(E.EVENTS_DIR.glob("*.jsonl")) if E.EVENTS_DIR.exists() else []
    non_demo = [p for p in logs if p.stem != "demo"]
    if non_demo:
        return False, f"real investigation topics present: {[p.stem for p in non_demo]}"
    demo = E.topic_path("demo")
    if not demo.exists() or demo.stat().st_size == 0:
        return True, "no seed present (already clean)"
    has_marker = False
    for raw in demo.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            if json.loads(raw).get("event") == "seed_marker":
                has_marker = True
                break
        except json.JSONDecodeError:
            return False, "demo.jsonl contains lines that are not the seed; refusing"
    if not has_marker:
        return False, "demo.jsonl has no seed_marker — looks hand-edited; refusing"
    return True, "demo seed detected"


def _delete(paths):
    removed = []
    for p in paths:
        if p.exists():
            p.unlink()
            removed.append(str(p.relative_to(ROOT)))
    return removed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reset the template for a real investigation.")
    ap.add_argument("--topic", help="name of the real investigation topic to initialize")
    ap.add_argument("--yes", action="store_true", help="actually do it (otherwise dry-run)")
    a = ap.parse_args(argv)

    ok, why = _demo_log_is_seed()
    if not ok:
        print(f"REFUSING: {why}", file=sys.stderr)
        print("This looks like a real corpus. Clean up manually if you truly intend to.", file=sys.stderr)
        sys.exit(2)

    targets = [E.topic_path("demo"), E.INDEX_PATH, E.CONFIG_PATH, SEED_EXAMPLE, SELF]
    if not a.yes:
        print(f"[dry-run] {why}. With --yes I would:")
        for p in targets:
            print(f"    delete {p.relative_to(ROOT)}" + ("  (this script)" if p == SELF else ""))
        if a.topic:
            print(f"    init topic '{a.topic}'")
        print("\nRe-run with --yes to proceed. (git is never touched.)")
        return

    removed = _delete([E.topic_path("demo"), E.INDEX_PATH, E.CONFIG_PATH, SEED_EXAMPLE])
    print("Removed seed artifacts:")
    for r in removed:
        print(f"    - {r}")

    if a.topic:
        E.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        E.topic_path(a.topic).touch()
        E.write_config({"topic": a.topic})
        P.build_index(a.topic).close()
        print(f"\nInitialized real topic '{a.topic}'. Active topic set.")

    print("\nNext steps to configure a real investigation:")
    print("  1. Edit CLAUDE.md — set the investigation's scope, domain rules, and authority policy.")
    print("  2. Extend FUNCTIONAL_PREDICATES in scripts/kb/events.py for your domain's single-valued relations.")
    print("  3. Point an agent at the /investigate skill (or run the lead agent) with your topic.")
    if not a.topic:
        print("  4. Run:  python scripts/kb.py init --topic <your-topic>")
    print("\nNote: this cleaned the WORKING TREE only. Seed content may still exist in")
    print("git history — if you need a pristine history, re-initialize git (rm -rf .git && git init).")

    # finally, delete this script itself
    SELF.unlink()
    print(f"\nRemoved {SELF.relative_to(ROOT)} (fresh_start is single-use).")


if __name__ == "__main__":
    main()
