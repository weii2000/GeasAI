from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


type MemoryKind = Literal["fact", "event"]


@dataclass(frozen=True)
class MemoryItem:
    kind: MemoryKind
    content: str
    subject: str | None = None
    happened_at: str | None = None


@dataclass(frozen=True)
class RawTurn:
    id: str
    user_message: str
    assistant_message: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    assistant_message TEXT NOT NULL,
    consolidated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    subject TEXT NOT NULL,
    content TEXT NOT NULL,
    source_turn_ids TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(subject, content)
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject,
    content,
    content=facts,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, subject, content)
    VALUES (new.id, new.subject, new.content);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, subject, content)
    VALUES ('delete', old.id, old.subject, old.content);
END;

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    summary TEXT NOT NULL,
    happened_at TEXT NOT NULL DEFAULT '',
    source_turn_ids TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(summary, happened_at)
);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    summary,
    content=events,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, summary) VALUES (new.id, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, summary)
    VALUES ('delete', old.id, old.summary);
END;
"""

_UNSEGMENTED = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
)


class SqliteMemoryStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=3000")
        self.connection.executescript(_SCHEMA)
        path.chmod(0o600)

    def close(self) -> None:
        self.connection.close()

    def has_memories(self) -> bool:
        row = self.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM facts) "
            "OR EXISTS(SELECT 1 FROM events)"
        ).fetchone()
        return bool(row[0])

    def search(
        self,
        query: str,
        *,
        fact_limit: int = 4,
        event_limit: int = 3,
    ) -> list[MemoryItem]:
        fts = _fts_query(query)
        if not fts:
            return []

        facts = self.connection.execute(
            "SELECT f.subject, f.content "
            "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts, fact_limit),
        ).fetchall()
        events = self.connection.execute(
            "SELECT e.summary, e.happened_at "
            "FROM events_fts JOIN events e ON e.id = events_fts.rowid "
            "WHERE events_fts MATCH ? "
            "ORDER BY rank, e.happened_at DESC LIMIT ?",
            (fts, event_limit),
        ).fetchall()
        return [
            *(
                MemoryItem(
                    kind="fact",
                    subject=row["subject"],
                    content=row["content"],
                )
                for row in facts
            ),
            *(
                MemoryItem(
                    kind="event",
                    content=row["summary"],
                    happened_at=row["happened_at"] or None,
                )
                for row in events
            ),
        ]

    def record_turn(
        self,
        turn_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO raw_turns "
                "(id, session_id, user_message, assistant_message) "
                "VALUES (?, ?, ?, ?)",
                (turn_id, session_id, user_message, assistant_message),
            )
            saved = self.connection.execute(
                "SELECT session_id, user_message, assistant_message "
                "FROM raw_turns WHERE id = ?",
                (turn_id,),
            ).fetchone()
            if tuple(saved) != (
                session_id,
                user_message,
                assistant_message,
            ):
                raise ValueError("turn id belongs to another exchange")

    def pending_turns(self, limit: int) -> list[RawTurn]:
        rows = self.connection.execute(
            "SELECT id, user_message, assistant_message FROM raw_turns "
            "WHERE consolidated = 0 ORDER BY created_at, rowid LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            RawTurn(
                id=row["id"],
                user_message=row["user_message"],
                assistant_message=row["assistant_message"],
            )
            for row in rows
        ]

    def save_consolidation(
        self,
        turns: list[RawTurn],
        facts: list[tuple[str, str]],
        events: list[tuple[str, str | None]],
    ) -> None:
        if not turns:
            return

        turn_ids = [turn.id for turn in turns]
        sources = json.dumps(turn_ids)
        placeholders = ",".join("?" for _ in turn_ids)
        with self.connection:
            self.connection.executemany(
                "INSERT INTO facts (subject, content, source_turn_ids) "
                "VALUES (?, ?, ?) ON CONFLICT(subject, content) DO NOTHING",
                ((subject, content, sources) for subject, content in facts),
            )
            self.connection.executemany(
                "INSERT INTO events (summary, happened_at, source_turn_ids) "
                "VALUES (?, ?, ?) ON CONFLICT(summary, happened_at) DO NOTHING",
                (
                    (summary, happened_at or "", sources)
                    for summary, happened_at in events
                ),
            )
            self.connection.execute(
                f"UPDATE raw_turns SET consolidated = 1 "
                f"WHERE id IN ({placeholders})",
                turn_ids,
            )


def _fts_query(text: str) -> str:
    words = re.findall(r"[^\W_]{2,}", text.casefold())
    return " OR ".join(
        f"{word}*" if _UNSEGMENTED.search(word) else word
        for word in dict.fromkeys(words)
    )
