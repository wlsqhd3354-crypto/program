"""영업 크롤러 데이터베이스 (SQLite).

테이블:
  leads     — 사이트에서 수집한 영업 대상
  contacts  — leads 에 대한 접촉 이력
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from paths import resource_path

DB_FILE = "leads.db"
DUPLICATE_MEMO_FILE = "duplicate_memo.txt"

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
    memo         TEXT,
    priority     TEXT NOT NULL DEFAULT '보통',
    next_action_at TEXT,
    duplicate_key TEXT,
    duplicate_of INTEGER,
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

STATUS_OPTIONS = ["미접촉", "시도중", "응답대기", "보류", "거절", "계약", "실패", "중복"]
PRIORITY_OPTIONS = ["낮음", "보통", "높음", "긴급"]
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
    memo: str = ""
    priority: str = "보통"
    next_action_at: str = ""
    duplicate_key: str = ""
    duplicate_of: Optional[int] = None
    matched_keywords: list[str] = field(default_factory=list)
    id: Optional[int] = None
    found_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Lead":
        keys = row.keys()
        body_text = row["body_text"] if "body_text" in keys else row["body_excerpt"]
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
            memo=row["memo"] if "memo" in keys and row["memo"] else "",
            priority=row["priority"] if "priority" in keys and row["priority"] else "보통",
            next_action_at=row["next_action_at"] if "next_action_at" in keys and row["next_action_at"] else "",
            duplicate_key=row["duplicate_key"] if "duplicate_key" in keys and row["duplicate_key"] else "",
            duplicate_of=row["duplicate_of"] if "duplicate_of" in keys else None,
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
        migrations = {
            "body_text": "ALTER TABLE leads ADD COLUMN body_text TEXT",
            "memo": "ALTER TABLE leads ADD COLUMN memo TEXT",
            "priority": "ALTER TABLE leads ADD COLUMN priority TEXT NOT NULL DEFAULT '보통'",
            "next_action_at": "ALTER TABLE leads ADD COLUMN next_action_at TEXT",
            "duplicate_key": "ALTER TABLE leads ADD COLUMN duplicate_key TEXT",
            "duplicate_of": "ALTER TABLE leads ADD COLUMN duplicate_of INTEGER",
        }
        for col, sql in migrations.items():
            if col not in cols:
                conn.execute(sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_next_action ON leads(next_action_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_duplicate_of ON leads(duplicate_of)")


def duplicate_memo_path() -> str:
    return resource_path(DUPLICATE_MEMO_FILE)


def ensure_duplicate_memo_file() -> str:
    path = duplicate_memo_path()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 중복으로 볼 전화/이메일/카톡/업체명/문구를 한 줄에 하나씩 적으세요.\n")
    return path


def load_duplicate_memo_entries() -> list[str]:
    path = ensure_duplicate_memo_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
    except OSError:
        return []


def append_duplicate_memo(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ensure_duplicate_memo_file()
    path = ensure_duplicate_memo_file()
    existing = {_normalize_blob(v) for v in load_duplicate_memo_entries()}
    if _normalize_blob(value) not in existing:
        with open(path, "a", encoding="utf-8") as f:
            f.write(value + "\n")
    return path


def _split_csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _normalize_blob(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _phone_key(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    return f"phone:{digits}" if len(digits) >= 8 else ""


def _text_key(prefix: str, value: str, min_len: int = 4) -> str:
    text = _normalize_blob(value)
    return f"{prefix}:{text}" if len(text) >= min_len else ""


def lead_duplicate_keys(lead: Lead) -> list[str]:
    keys: list[str] = []
    for phone in lead.phones:
        key = _phone_key(phone)
        if key:
            keys.append(key)
    for email in lead.emails:
        key = _text_key("email", email, min_len=6)
        if key:
            keys.append(key)
    for kakao in lead.kakao_ids:
        key = _text_key("kakao", kakao, min_len=4)
        if key:
            keys.append(key)
    for chat in lead.open_chats:
        key = _text_key("openchat", chat, min_len=8)
        if key:
            keys.append(key)
    return list(dict.fromkeys(keys))


def primary_duplicate_value(lead: Lead) -> str:
    for values in (lead.phones, lead.emails, lead.kakao_ids, lead.open_chats):
        if values:
            return values[0]
    return lead.company or lead.writer or lead.title or lead.post_url


def _row_duplicate_keys(row: sqlite3.Row) -> set[str]:
    lead = Lead(
        site=row["site"],
        post_url=row["post_url"],
        phones=_split_csv(row["phones"]),
        emails=_split_csv(row["emails"]),
        kakao_ids=_split_csv(row["kakao_ids"]),
        open_chats=_split_csv(row["open_chats"]),
    )
    return set(lead_duplicate_keys(lead))


def _find_existing_duplicate(conn: sqlite3.Connection, lead: Lead) -> tuple[str, Optional[int]]:
    own_keys = set(lead_duplicate_keys(lead))
    if not own_keys:
        return "", None
    rows = conn.execute(
        """SELECT id, site, post_url, phones, emails, kakao_ids, open_chats
           FROM leads
           WHERE post_url <> ?""",
        (lead.post_url,),
    ).fetchall()
    for row in rows:
        matched = own_keys & _row_duplicate_keys(row)
        if matched:
            key = sorted(matched)[0]
            return key, row["id"]
    return "", None


def _find_memo_duplicate(lead: Lead) -> str:
    blob = _normalize_blob(
        " ".join(
            [
                lead.site,
                lead.post_url,
                lead.board,
                lead.category,
                lead.title,
                lead.body_excerpt,
                lead.body_text,
                lead.writer,
                lead.company,
                ",".join(lead.kakao_ids),
                ",".join(lead.open_chats),
                ",".join(lead.phones),
                ",".join(lead.emails),
            ]
        )
    )
    for entry in load_duplicate_memo_entries():
        normalized = _normalize_blob(entry)
        if normalized and normalized in blob:
            return f"memo:{entry[:80]}"
    return ""


def upsert_lead(lead: Lead) -> int:
    """post_url 기준 중복 방지. 기존 있으면 일부 필드만 갱신.
    반환: lead.id"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, status, found_at FROM leads WHERE post_url = ?", (lead.post_url,)
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
            lead.id = existing["id"]
            lead.status = existing["status"]
            lead.found_at = existing["found_at"] or ""
            lead.updated_at = now
            return existing["id"]
        memo_dup_key = _find_memo_duplicate(lead)
        contact_dup_key, duplicate_of = _find_existing_duplicate(conn, lead)
        if memo_dup_key:
            lead.duplicate_key = memo_dup_key
            lead.duplicate_of = None
        elif contact_dup_key:
            lead.duplicate_key = contact_dup_key
            lead.duplicate_of = duplicate_of
        if lead.duplicate_key and lead.status == "미접촉":
            lead.status = "중복"
        if lead.priority not in PRIORITY_OPTIONS:
            lead.priority = "보통"
        cur = conn.execute(
            """INSERT INTO leads
               (site, post_url, board, category, title, body_excerpt, body_text, writer, posted_at,
                kakao_ids, open_chats, phones, emails, company, status, memo, priority,
                next_action_at, duplicate_key, duplicate_of, matched_keywords, found_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                lead.memo or None,
                lead.priority or "보통",
                lead.next_action_at or None,
                lead.duplicate_key or None,
                lead.duplicate_of,
                ",".join(lead.matched_keywords) or None,
                now, now,
            ),
        )
        lead.id = cur.lastrowid
        lead.found_at = now
        lead.updated_at = now
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
        where.append(
            """(title LIKE ? OR body_excerpt LIKE ? OR body_text LIKE ? OR company LIKE ?
                OR memo LIKE ? OR phones LIKE ? OR emails LIKE ? OR kakao_ids LIKE ? OR open_chats LIKE ?)"""
        )
        kw = f"%{keyword}%"; params.extend([kw, kw, kw, kw, kw, kw, kw, kw, kw])
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


def update_lead_crm(
    lead_id: int,
    *,
    status: str | None = None,
    memo: str | None = None,
    priority: str | None = None,
    next_action_at: str | None = None,
    duplicate_key: str | None = None,
    duplicate_of: int | None = None,
):
    fields: list[str] = []
    params: list[object] = []
    if status is not None:
        if status not in STATUS_OPTIONS:
            raise ValueError(f"unknown status: {status}")
        fields.append("status = ?"); params.append(status)
    if memo is not None:
        fields.append("memo = ?"); params.append(memo.strip() or None)
    if priority is not None:
        if priority not in PRIORITY_OPTIONS:
            raise ValueError(f"unknown priority: {priority}")
        fields.append("priority = ?"); params.append(priority)
    if next_action_at is not None:
        fields.append("next_action_at = ?"); params.append(next_action_at.strip() or None)
    if duplicate_key is not None:
        fields.append("duplicate_key = ?"); params.append(duplicate_key.strip() or None)
    if duplicate_of is not None:
        fields.append("duplicate_of = ?"); params.append(duplicate_of)
    if not fields:
        return
    fields.append("updated_at = ?")
    params.append(datetime.now().isoformat(timespec="seconds"))
    params.append(lead_id)
    with get_db() as conn:
        conn.execute(f"UPDATE leads SET {', '.join(fields)} WHERE id = ?", params)


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
        out = {"total": 0, "by_site": {}, "by_status": {}, "contactable": 0, "duplicates": 0, "due": 0}
        out["total"] = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        for r in conn.execute("SELECT site, COUNT(*) c FROM leads GROUP BY site").fetchall():
            out["by_site"][r["site"]] = r["c"]
        for r in conn.execute("SELECT status, COUNT(*) c FROM leads GROUP BY status").fetchall():
            out["by_status"][r["status"]] = r["c"]
        out["contactable"] = conn.execute(
            """SELECT COUNT(*) FROM leads
               WHERE COALESCE(kakao_ids, '') <> ''
                  OR COALESCE(open_chats, '') <> ''
                  OR COALESCE(phones, '') <> ''
                  OR COALESCE(emails, '') <> ''"""
        ).fetchone()[0]
        out["duplicates"] = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = '중복' OR COALESCE(duplicate_key, '') <> ''"
        ).fetchone()[0]
        out["due"] = conn.execute(
            """SELECT COUNT(*) FROM leads
               WHERE COALESCE(next_action_at, '') <> ''
                 AND next_action_at <= ?
                 AND status NOT IN ('계약', '실패', '거절', '중복')""",
            (datetime.now().strftime("%Y-%m-%d"),),
        ).fetchone()[0]
        return out


if __name__ == "__main__":
    init_db()
    print("DB initialized at", resource_path(DB_FILE))
    print("stats:", stats())
