"""Investigative Knowledge Base — pure-stdlib toolkit.

Canonical store is an append-only JSONL event log (kb/events/<topic>.jsonl).
The SQLite index (kb/index.db) is a disposable read-model rebuilt by replaying
events. Nothing is ever mutated or deleted in the log; belief change is a new
event. See docs/methodology.md for the mapping to the research report.
"""

__version__ = "1.0.0"
