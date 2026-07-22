"""Event log: the canonical, append-only store.

Every belief change is an event, one JSON object per line, in
kb/events/<topic>.jsonl. Nothing is mutated or deleted. IDs are
content-addressed and assigned HERE (never by the agent) so the same
proposition always maps to the same claim_id (enables dedup + stable
references across supersession/retraction).

Design rules enforced in this module:
  - Timestamps (`t_created`) are script-set, UTC, ISO-8601 (S10).
  - Replay order is file append offset; `t_created` is data (S9).
  - Every event carries `v` = SCHEMA_VERSION (S8).
  - append_event takes a cross-platform advisory lock: single-writer (S7).
  - S-P-O / URIs are normalized (casefold + whitespace-collapse + NFC)
    before hashing, so dedup is exact-normalized-match only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import unicodedata
from pathlib import Path

SCHEMA_VERSION = 1

# repo root = parents[2] of scripts/kb/events.py  ->  scripts/kb -> scripts -> root
ROOT = Path(__file__).resolve().parents[2]
KB_DIR = ROOT / "kb"
EVENTS_DIR = KB_DIR / "events"
CONFIG_PATH = KB_DIR / "config.json"
INDEX_PATH = KB_DIR / "index.db"

# ---------------------------------------------------------------- vocab

# Epistemic status vocabulary (report §C). Ordered weakest -> strongest,
# plus the terminal/negative states.
STATUSES = [
    "hypothesized", "probable", "corroborated", "confirmed",
    "disputed", "needs_review", "superseded", "retracted",
]
# States in which a claim is currently "believed" for as-of queries.
BELIEVED = {"probable", "corroborated", "confirmed"}

# Legal status transitions (S6). retracted/superseded are terminal except
# for explicit revival (retracted -> hypothesized) when the proposition is
# re-asserted with fresh evidence.
TRANSITIONS = {
    "hypothesized": {"probable", "corroborated", "confirmed", "disputed", "needs_review", "retracted"},
    "probable":     {"corroborated", "confirmed", "disputed", "needs_review", "retracted", "superseded"},
    "corroborated": {"confirmed", "disputed", "needs_review", "retracted", "superseded", "probable"},
    "confirmed":    {"disputed", "needs_review", "retracted", "superseded"},
    "disputed":     {"corroborated", "confirmed", "needs_review", "retracted", "superseded", "probable"},
    "needs_review": {"probable", "corroborated", "confirmed", "disputed", "retracted", "superseded"},
    "superseded":   set(),                 # terminal
    "retracted":    {"hypothesized"},      # terminal except explicit revival
}

# ICD-203 seven-band likelihood ladder (report §C). Kept SEPARATE from
# analytic confidence — two fields, never combined.
LIKELIHOOD_BANDS = [
    "almost_no_chance", "very_unlikely", "unlikely", "roughly_even",
    "likely", "very_likely", "almost_certain",
]
ANALYTIC_CONFIDENCE = ["high", "mod", "low"]

# How a confidence_score was derived. `self_report` alone is uncalibrated
# and is flagged by lint (report §C: LLM verbalized confidence is
# systematically overconfident).
CONFIDENCE_METHODS = [
    "self_report", "self_consistency", "multi_agent_agreement",
    "source_corroboration", "human",
]

CONFLICT_TYPES = [
    "no_conflict", "complementary", "conflicting_opinions",
    "outdated", "misinformation", "factual",
]

# Predicates treated as single-valued: a differing object for the same
# subject is a genuine contradiction (drives the silent-resolution lint, S4).
# Operators extend this list for their domain.
FUNCTIONAL_PREDICATES = {
    "was_born_in", "died_in", "date_of_birth", "date_of_death",
    "capital_of", "headquartered_in", "ceo_is", "founded_in",
    "population_of", "height_of", "is_a",
}

EVENT_TYPES = {
    "seed_marker", "agent_run_started",
    "source_registered", "source_discredited", "source_recredited",
    "claim_created", "evidence_added", "status_changed",
    "confidence_recalibrated", "conflict_linked", "resolution_updated",
    "claim_invalidated", "claim_superseded", "claim_retracted",
    "rebuttal_triggered",
}

# ---------------------------------------------------------------- lock

try:                      # POSIX
    import fcntl

    def _lock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
except ImportError:       # Windows
    import msvcrt

    def _lock(fh):
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(fh):
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

# ---------------------------------------------------------------- time / norm


def now_iso() -> str:
    """Script-set UTC ISO-8601 timestamp (S10)."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


def to_utc_iso(s) -> str | None:
    """Parse a tz-aware (or naive-assumed-UTC) timestamp and re-emit it as a
    canonical UTC ISO-8601 string, so world-time fields sort chronologically
    under plain string comparison. Raises ValueError on unparseable input."""
    if s is None or s == "":
        return None
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    dt = _dt.datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="microseconds")


def normalize(text: str) -> str:
    """Casefold + NFC + whitespace-collapse. Basis for content-addressed IDs
    and exact-normalized dedup."""
    if text is None:
        return ""
    t = unicodedata.normalize("NFC", str(text)).strip().casefold()
    return re.sub(r"\s+", " ", t)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def claim_id(topic: str, subj: str, pred: str, obj: str) -> str:
    key = f"{normalize(topic)}|{normalize(subj)}|{normalize(pred)}|{normalize(obj)}"
    return "clm_" + _sha(key)


def source_id(uri: str) -> str:
    return "src_" + _sha(normalize(uri))


def evidence_id(claim: str, src: str, locator: str) -> str:
    return "evd_" + _sha(f"{claim}|{src}|{normalize(locator)}")


# ---------------------------------------------------------------- config


def read_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def write_config(cfg: dict) -> None:
    KB_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def active_topic(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    cfg = read_config()
    if "topic" not in cfg:
        raise SystemExit("No active topic. Run:  python scripts/kb.py init --topic <name>")
    return cfg["topic"]


def topic_path(topic: str) -> Path:
    return EVENTS_DIR / f"{topic}.jsonl"


# ---------------------------------------------------------------- append / read


def append_event(event: str, payload: dict, *, topic: str | None = None,
                 agent_run: str | None = None) -> dict:
    """Append one event to the topic log under an advisory lock (S7).

    `event` is the type; `payload` the type-specific body. Returns the full
    stored record. event_id is a hash of the full record so identical repeats
    at the same microsecond collapse.
    """
    if event not in EVENT_TYPES:
        raise ValueError(f"Unknown event type: {event!r}")
    topic = active_topic(topic)
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = topic_path(topic)

    rec = {
        "v": SCHEMA_VERSION,
        "event": event,
        "topic": topic,
        "t_created": now_iso(),
        "agent_run": agent_run,
        **payload,
    }
    rec["event_id"] = "evt_" + _sha(json.dumps(rec, sort_keys=True, ensure_ascii=False))

    line = json.dumps(rec, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        _lock(fh)
        try:
            fh.write(line + "\n")
            fh.flush()
        finally:
            _unlock(fh)
    return rec


class EventLogError(Exception):
    pass


def read_events(topic: str | None = None, *, as_of: str | None = None):
    """Yield events in append order (S9). `as_of` (ISO string) filters to
    events with t_created <= as_of — the system-time cutoff that powers
    `query as-of --system`. Malformed lines fail loud with a line number (S8).
    """
    topic = active_topic(topic)
    path = topic_path(topic)
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            if raw.startswith("<<<<<<<") or raw.startswith(">>>>>>>") or raw.startswith("======="):
                raise EventLogError(
                    f"{path}:{lineno}: git merge-conflict marker in event log; resolve by hand.")
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                raise EventLogError(f"{path}:{lineno}: malformed JSON event: {e}") from e
            if rec.get("event") not in EVENT_TYPES:
                raise EventLogError(f"{path}:{lineno}: unknown event type {rec.get('event')!r}")
            if as_of is not None and rec.get("t_created", "") > as_of:
                continue
            yield rec
