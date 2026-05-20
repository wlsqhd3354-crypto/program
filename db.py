"""영업 크롤러 데이터베이스 (SQLite).

테이블:
  leads     — 사이트에서 수집한 영업 대상
  contacts  — leads 에 대한 접촉 이력
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from paths import resource_path

DB_FILE = "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    site         TEXT NOT NULL,                -- sellclub / mamentor / iboss
    post_url     TEXT NOT NULL UNIQUE,         -- 중복방지
    board        TEXT,                          -- 게시판 코드 (bo_table or BD2986)
    category     TEXT,                          -- 카테고리 / 분류
    title        TEXT,
    body_excerpt TEXT,
    body_text    TEXT,
    writer       TEXT,                          -- 작성자 닉네임/ID
    posted_at    TEXT,                          -- 게시 일시 (사이트 표시 그대로)
    kakao_ids    TEXT,                          -- JSON 배열 (쉼표 구분)
    open_chats   TEXT,                          -- JSON 배열
    phones       TEXT,                          -- 쉼표 구분
    emails       TEXT,                          -- 쉼표 구분
    company      TEXT,
    status       TEXT NOT NULL DEFAULT '미접촉', -- 미접촉 / 시도중 / 응답대기 / 거절 / 계약 / 실패
    matched_keywords TEXT,                       -- 매칭된 키워드 (쉼표)
    found_at     TEXT NOT NULL,                  -- 수집 시각 (ISO)
    updated_at   TEXT NOT NULL                   -- 마지막 갱신
);

CREATE INDEX IF NOT EXISTS idx_leads_site ON leads(site);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_found ON leads(found_at DESC);

CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL,
    attempted_at TEXT NOT NULL,
    channel    TEXT NOT NULL,                  -- 카톡 / 전화 / 이메일 / 쪽지 / 오픈채팅
    result     TEXT NOT NULL,                  -- 성공 / 실패 / 무응답
    note       TEXT,
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_contacts_lead ON contacts(lead_id);
"""

STATUS_OPTIONS = ["미접촉", "시도중", "응답대기", "거절", "계약", "실패"]
CHANNEL_OPTIONS = ["카톡", "전화", "이메일", "쪽지", "오픈채팅"]
RESULT_OPTIONS = ["성공", "실패", "무응답"]


@dataclass
class Lead:
    site: str
    post_url: str
    board: str = ""
    category: str = ""
    title: str = ""
    body_excerpt: str = ""
    body_text: str = ""
    writer: str = ""
    posted_at: str = ""
    kakao_ids: list[str] = field(default_factory=list)
    open_chats: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    company: str = ""
    status: str = "미접촉"
    matched_keywords: list[str] = field(default_factory=list)
    id: Optional[int] = None
    found_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Lead":
        body_text = row["body_text"] if "body_text" in row.keys() else row["body_excerpt"]
        return cls(
            id=row["id"],
            site=row["site"],
            post_url=row["post_url"],
            board=row["board"] or "",
            category=row["category"] or "",
            title=row["title"] or "",
            body_excerpt=row["body_excerpt"] or "",
            body_text=body_text or "",
            writer=row["writer"] or "",
            posted_at=row["posted_at"] or "",
            kakao_ids=row["kakao_ids"].split(",") if row["kakao_ids"] else [],
            open_chats=row["open_chats"].split(",") if row["open_chats"] else [],
            phones=row["phones"].split(",") if row["phones"] else [],
            emails=row["emails"].split(",") if row["emails"] else [],
            company=row["company"] or "",
            status=row["status"],
            matched_keywords=row["matched_keywords"].split(",") if row["matched_keywords"] else [],
            found_at=row["found_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Contact:
    lead_id: int
    attempted_at: str
    channel: str
    result: str
    note: str = ""
    id: Optional[int] = None


@contextmanager
def get_db():
    path = resource_path(DB_FILE)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
        if "body_text" not in cols:
            conn.execute("ALTER TABLE leads ADD COLUMN body_text TEXT")


def upsert_lead(lead: Lead) -> int:
    """post_url 기준 중복 방지. 기존 있으면 일부 필드만 갱신.
    반환: lead.id"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, status FROM leads WHERE post_url = ?", (lead.post_url,)
        ).fetchone()
        if existing:
            # 본문/연락처/매칭키워드 등 새로 발견한 정보가 있으면 갱신
            conn.execute(
                """UPDATE leads SET
                    title = ?, body_excerpt = ?, body_text = ?, writer = ?, posted_at = ?,
                    kakao_ids = ?, open_chats = ?, phones = ?, emails = ?, company = ?,
                    category = ?, matched_keywords = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    lead.title or None, lead.body_excerpt or None, lead.body_text or None,
                    lead.writer or None, lead.posted_at or None,
                    ",".join(lead.kakao_ids) or None,
                    ",".join(lead.open_chats) or None,
                    ",".join(lead.phones) or None,
                    ",".join(lead.emails) or None,
                    lead.company or None,
                    lead.category or None,
                    ",".join(lead.matched_keywords) or None,
                    now,
                    existing["id"],
                ),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO leads
               (site, post_url, board, category, title, body_excerpt, body_text, writer, posted_at,
                kakao_ids, open_chats, phones, emails, company, status, matched_keywords,
                found_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lead.site, lead.post_url, lead.board or None, lead.category or None,
                lead.title or None, lead.body_excerpt or None, lead.body_text or None,
                lead.writer or None, lead.posted_at or None,
                ",".join(lead.kakao_ids) or None,
                ",".join(lead.open_chats) or None,
                ",".join(lead.phones) or None,
                ",".join(lead.emails) or None,
                lead.company or None,
                lead.status,
                ",".join(lead.matched_keywords) or None,
                now, now,
            ),
        )
        return cur.lastrowid


def get_leads(
    site: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    limit: int = 500,
    order: str = "found_at DESC",
) -> list[Lead]:
    sql = "SELECT * FROM leads"
    where, params = [], []
    if site:
        where.append("site = ?"); params.append(site)
    if status:
        where.append("status = ?"); params.append(status)
    if keyword:
        where.append("(title LIKE ? OR body_excerpt LIKE ? OR body_text LIKE ? OR company LIKE ?)")
        kw = f"%{keyword}%"; params.extend([kw, kw, kw, kw])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [Lead.from_row(r) for r in rows]


def get_lead(lead_id: int) -> Optional[Lead]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return Lead.from_row(row) if row else None


def update_status(lead_id: int, status: str):
    if status not in STATUS_OPTIONS:
        raise ValueError(f"unknown status: {status}")
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, lead_id),
        )


def add_contact(c: Contact) -> int:
    if c.channel not in CHANNEL_OPTIONS:
        raise ValueError(f"unknown channel: {c.channel}")
    if c.result not in RESULT_OPTIONS:
        raise ValueError(f"unknown result: {c.result}")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO contacts (lead_id, attempted_at, channel, result, note) VALUES (?, ?, ?, ?, ?)",
            (c.lead_id, c.attempted_at, c.channel, c.result, c.note or None),
        )
        # status 자동 업데이트: 마지막 시도 결과 기반
        new_status = {"성공": "계약", "실패": "실패", "무응답": "응답대기"}.get(c.result, "시도중")
        conn.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, c.attempted_at, c.lead_id),
        )
        return cur.lastrowid


def get_contacts(lead_id: int) -> list[Contact]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM contacts WHERE lead_id = ? ORDER BY attempted_at DESC",
            (lead_id,),
        ).fetchall()
        return [
            Contact(
                id=r["id"], lead_id=r["lead_id"], attempted_at=r["attempted_at"],
                channel=r["channel"], result=r["result"], note=r["note"] or "",
            )
            for r in rows
        ]


def stats() -> dict:
    """전체 현황."""
    with get_db() as conn:
        out = {"total": 0, "by_site": {}, "by_status": {}}
        out["total"] = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        for r in conn.execute("SELECT site, COUNT(*) c FROM leads GROUP BY site").fetchall():
            out["by_site"][r["site"]] = r["c"]
        for r in conn.execute("SELECT status, COUNT(*) c FROM leads GROUP BY status").fetchall():
            out["by_status"][r["status"]] = r["c"]
        return out


if __name__ == "__main__":
    init_db()
    print("DB initialized at", resource_path(DB_FILE))
    print("stats:", stats())
