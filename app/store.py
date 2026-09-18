"""Хранилище на sqlite3 из стандартной библиотеки.

Одна точка доступа к данным: схема, миграция и запросы. Внешних ORM нет
намеренно — проект должен подниматься на чистом Python без установки пакетов.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.domain import (
    ActionKind,
    ActionStatus,
    Category,
    ChatInfo,
    Decision,
    IncomingMessage,
    Resolution,
    TicketStatus,
    utcnow,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id                 INTEGER PRIMARY KEY,
    external_id        TEXT NOT NULL UNIQUE,
    title              TEXT NOT NULL,
    is_group           INTEGER NOT NULL DEFAULT 1,
    is_muted           INTEGER NOT NULL DEFAULT 0,
    is_request_channel INTEGER NOT NULL DEFAULT 0,
    write_allowed      INTEGER NOT NULL DEFAULT 0,
    first_seen_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                 INTEGER PRIMARY KEY,
    chat_id            INTEGER NOT NULL REFERENCES chats(id),
    external_id        TEXT NOT NULL,
    author_name        TEXT NOT NULL,
    author_external_id TEXT,
    text               TEXT NOT NULL DEFAULT '',
    sent_at            TEXT NOT NULL,
    seen_at            TEXT NOT NULL,
    has_attachments    INTEGER NOT NULL DEFAULT 0,
    own_reactions      TEXT NOT NULL DEFAULT '[]',
    ticket_id          INTEGER REFERENCES tickets(id),
    UNIQUE (chat_id, external_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    id                 INTEGER PRIMARY KEY,
    chat_id            INTEGER NOT NULL REFERENCES chats(id),
    chat_title         TEXT NOT NULL DEFAULT '',
    requester_name     TEXT NOT NULL,
    requester_external_id TEXT,
    title              TEXT NOT NULL,
    original_text      TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    category           TEXT NOT NULL DEFAULT 'unknown',
    resolution         TEXT NOT NULL DEFAULT 'needs_human',
    status             TEXT NOT NULL DEFAULT 'new',
    confidence         REAL NOT NULL DEFAULT 0,
    classified_by      TEXT NOT NULL DEFAULT 'rules',
    entities           TEXT NOT NULL DEFAULT '{}',
    work_log           TEXT NOT NULL DEFAULT '[]',
    kb_slug            TEXT,
    user_confirmed     INTEGER NOT NULL DEFAULT 0,
    source_message_id  TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    resolved_at        TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id                 INTEGER PRIMARY KEY,
    ticket_id          INTEGER NOT NULL REFERENCES tickets(id),
    kind               TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'proposed',
    payload            TEXT NOT NULL DEFAULT '{}',
    rationale          TEXT NOT NULL DEFAULT '',
    result             TEXT NOT NULL DEFAULT '',
    requires_approval  INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    executed_at        TEXT
);

CREATE TABLE IF NOT EXISTS rate_events (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tickets_status  ON tickets(status);
CREATE INDEX IF NOT EXISTS ix_actions_status  ON actions(status);
CREATE INDEX IF NOT EXISTS ix_messages_ticket ON messages(ticket_id);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


class Store:
    """Потокобезопасная обёртка над одним sqlite-файлом."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- базовое
    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------------------------------------------- чаты
    def upsert_chat(self, chat: ChatInfo, *, is_request_channel: bool = False) -> int:
        """Регистрирует чат. Разрешение на запись выдаётся только личным диалогам."""
        row = self._one("SELECT id FROM chats WHERE external_id = ?", (chat.external_id,))
        write_allowed = 0 if (chat.is_group or is_request_channel) else 1
        if row:
            self._exec(
                "UPDATE chats SET title=?, is_group=?, is_muted=?, is_request_channel=?,"
                " write_allowed=? WHERE id=?",
                (chat.title, int(chat.is_group), int(chat.is_muted),
                 int(is_request_channel), write_allowed, row["id"]),
            )
            return int(row["id"])
        cur = self._exec(
            "INSERT INTO chats (external_id, title, is_group, is_muted, is_request_channel,"
            " write_allowed, first_seen_at) VALUES (?,?,?,?,?,?,?)",
            (chat.external_id, chat.title, int(chat.is_group), int(chat.is_muted),
             int(is_request_channel), write_allowed, _iso(utcnow())),
        )
        return int(cur.lastrowid)

    def chat_by_external(self, external_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM chats WHERE external_id = ?", (external_id,))

    def is_write_allowed(self, chat_external_id: str) -> bool:
        """Главный предохранитель: в чат заявок бот не пишет никогда."""
        row = self.chat_by_external(chat_external_id)
        return bool(row and row["write_allowed"])

    # ------------------------------------------------------------- сообщения
    def message_exists(self, chat_id: int, external_id: str) -> bool:
        return self._one(
            "SELECT 1 FROM messages WHERE chat_id=? AND external_id=?", (chat_id, external_id)
        ) is not None

    def save_message(self, chat_id: int, msg: IncomingMessage) -> int | None:
        """Сохраняет сообщение. None — если такое уже есть (дубликат опроса)."""
        if self.message_exists(chat_id, msg.external_id):
            return None
        cur = self._exec(
            "INSERT INTO messages (chat_id, external_id, author_name, author_external_id, text,"
            " sent_at, seen_at, has_attachments, own_reactions) VALUES (?,?,?,?,?,?,?,?,?)",
            (chat_id, msg.external_id, msg.author_name, msg.author_external_id, msg.text,
             _iso(msg.sent_at), _iso(utcnow()), int(msg.has_attachments),
             json.dumps(list(msg.own_reactions), ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def link_message_ticket(self, message_id: int, ticket_id: int) -> None:
        self._exec("UPDATE messages SET ticket_id=? WHERE id=?", (ticket_id, message_id))

    # ---------------------------------------------------------------- тикеты
    def create_ticket(
        self, *, chat_id: int, chat_title: str, msg: IncomingMessage, decision: Decision
    ) -> int:
        now = _iso(utcnow())
        cur = self._exec(
            "INSERT INTO tickets (chat_id, chat_title, requester_name, requester_external_id,"
            " title, original_text, description, category, resolution, status, confidence,"
            " classified_by, entities, work_log, kb_slug, source_message_id, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, chat_title, msg.author_name, msg.author_external_id, decision.title,
             msg.text, decision.summary, decision.category.value, decision.resolution.value,
             TicketStatus.new.value, decision.confidence, decision.classified_by,
             json.dumps(decision.entities, ensure_ascii=False), "[]", decision.kb_slug,
             msg.external_id, now, now),
        )
        return int(cur.lastrowid)

    def ticket(self, ticket_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM tickets WHERE id=?", (ticket_id,))

    def tickets(self, *, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if status:
            return self._all(
                "SELECT * FROM tickets WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
            )
        return self._all("SELECT * FROM tickets ORDER BY id DESC LIMIT ?", (limit,))

    def open_ticket_for(self, chat_id: int, requester_name: str) -> sqlite3.Row | None:
        """Открытая заявка этого человека — сначала в том же чате, потом в любом.

        Искать только в том же чате нельзя: разговор начинается в чате заявок, а
        продолжается в личке («номер кабинета 305», «код аспии …»). Без поиска
        по всем чатам каждое такое уточнение заводило бы новую заявку.
        """
        closed = (TicketStatus.resolved.value, TicketStatus.closed_by_human.value,
                  TicketStatus.cancelled.value)
        placeholders = ",".join("?" * len(closed))
        same_chat = self._one(
            f"SELECT * FROM tickets WHERE chat_id=? AND requester_name=?"
            f" AND status NOT IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (chat_id, requester_name, *closed),
        )
        if same_chat is not None:
            return same_chat
        return self._one(
            f"SELECT * FROM tickets WHERE requester_name=?"
            f" AND status NOT IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            (requester_name, *closed),
        )

    def chat_external_id(self, chat_id: int) -> str:
        """Внешний идентификатор чата по внутреннему — нужен для реакций."""
        row = self._one("SELECT external_id FROM chats WHERE id=?", (chat_id,))
        return str(row["external_id"]) if row else ""

    def set_status(self, ticket_id: int, status: TicketStatus) -> None:
        resolved = _iso(utcnow()) if status in (
            TicketStatus.resolved, TicketStatus.closed_by_human
        ) else None
        self._exec(
            "UPDATE tickets SET status=?, updated_at=?,"
            " resolved_at=COALESCE(?, resolved_at) WHERE id=?",
            (status.value, _iso(utcnow()), resolved, ticket_id),
        )

    def set_user_confirmed(self, ticket_id: int, value: bool = True) -> None:
        self._exec(
            "UPDATE tickets SET user_confirmed=?, updated_at=? WHERE id=?",
            (int(value), _iso(utcnow()), ticket_id),
        )

    def log(self, ticket_id: int, text: str) -> None:
        """Добавляет запись в журнал «что бот сделал для решения»."""
        row = self.ticket(ticket_id)
        if not row:
            return
        entries = json.loads(row["work_log"])
        entries.append({"at": _iso(utcnow()), "text": text})
        self._exec(
            "UPDATE tickets SET work_log=?, updated_at=? WHERE id=?",
            (json.dumps(entries, ensure_ascii=False), _iso(utcnow()), ticket_id),
        )

    def update_classification(self, ticket_id: int, decision: Decision) -> None:
        self._exec(
            "UPDATE tickets SET category=?, resolution=?, confidence=?, classified_by=?,"
            " entities=?, kb_slug=?, description=?, updated_at=? WHERE id=?",
            (decision.category.value, decision.resolution.value, decision.confidence,
             decision.classified_by, json.dumps(decision.entities, ensure_ascii=False),
             decision.kb_slug, decision.summary, _iso(utcnow()), ticket_id),
        )

    # -------------------------------------------------------------- действия
    def add_action(
        self, ticket_id: int, kind: ActionKind, payload: dict, rationale: str,
        *, requires_approval: bool = True, status: ActionStatus = ActionStatus.proposed,
    ) -> int:
        cur = self._exec(
            "INSERT INTO actions (ticket_id, kind, status, payload, rationale,"
            " requires_approval, created_at) VALUES (?,?,?,?,?,?,?)",
            (ticket_id, kind.value, status.value, json.dumps(payload, ensure_ascii=False),
             rationale, int(requires_approval), _iso(utcnow())),
        )
        return int(cur.lastrowid)

    def action(self, action_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM actions WHERE id=?", (action_id,))

    def actions_for(self, ticket_id: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM actions WHERE ticket_id=? ORDER BY id", (ticket_id,))

    def pending_actions(self, limit: int = 200) -> list[sqlite3.Row]:
        """Очередь на одобрение оператором."""
        return self._all(
            "SELECT a.*, t.title AS ticket_title, t.requester_name, t.chat_title"
            " FROM actions a JOIN tickets t ON t.id = a.ticket_id"
            " WHERE a.status='proposed' ORDER BY a.id LIMIT ?",
            (limit,),
        )

    def approved_actions(self, limit: int = 50) -> list[sqlite3.Row]:
        """Одобренные, но ещё не исполненные — их забирает исполнитель."""
        return self._all(
            "SELECT * FROM actions WHERE status='approved' ORDER BY id LIMIT ?", (limit,)
        )

    def set_action_status(
        self, action_id: int, status: ActionStatus, result: str = ""
    ) -> None:
        executed = _iso(utcnow()) if status in (ActionStatus.done, ActionStatus.failed) else None
        self._exec(
            "UPDATE actions SET status=?, result=COALESCE(NULLIF(?, ''), result),"
            " executed_at=COALESCE(?, executed_at) WHERE id=?",
            (status.value, result, executed, action_id),
        )

    # ------------------------------------------------------------- лимиты
    def note_external_action(self, kind: str) -> None:
        self._exec(
            "INSERT INTO rate_events (kind, created_at) VALUES (?,?)", (kind, _iso(utcnow()))
        )

    def actions_last_hour(self) -> int:
        since = _iso(utcnow() - timedelta(hours=1)) or ""
        row = self._one("SELECT COUNT(*) AS n FROM rate_events WHERE created_at >= ?", (since,))
        return int(row["n"]) if row else 0

    # --------------------------------------------------------------- сводка
    def stats(self) -> dict[str, int]:
        counts = {r["status"]: r["n"] for r in self._all(
            "SELECT status, COUNT(*) AS n FROM tickets GROUP BY status")}
        total = sum(counts.values())
        return {
            "total": total,
            "open": total - counts.get(TicketStatus.resolved.value, 0)
            - counts.get(TicketStatus.closed_by_human.value, 0)
            - counts.get(TicketStatus.cancelled.value, 0),
            "resolved": counts.get(TicketStatus.resolved.value, 0),
            "pending_actions": len(self.pending_actions()),
            "actions_last_hour": self.actions_last_hour(),
        }


def row_to_dict(row: sqlite3.Row | None) -> dict:
    """Строку БД — в удобный dict с распакованным JSON."""
    if row is None:
        return {}
    data = dict(row)
    for key in ("entities", "work_log", "payload", "own_reactions"):
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    for key, enum_cls in (("category", Category), ("resolution", Resolution),
                          ("status", None)):
        if key in data and enum_cls is not None:
            try:
                data[key] = enum_cls(data[key])
            except ValueError:
                pass
    return data
