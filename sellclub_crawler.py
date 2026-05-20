"""셀클럽 크롤러.

목록: /community/bbs/board.php?bo_table=maket_5_3&page=N
상세: /community/bbs/board.php?bo_table=maket_5_3&wr_id=NNNNNN

비로그인 시 연락처/이메일이 'Member Only' 로 마스킹되므로
SellClubClient (로그인 세션) 을 주입받아 사용.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse, parse_qs
from typing import Callable, Optional

from base import BoardClient
from config import SELLCLUB_BASE
from crawler_base import BaseCrawler, CrawlConfig, sleep_jitter, matches_keywords, should_stop
from db import Lead, upsert_lead
from extractor import (
    ContactInfo, extract_contacts, html_to_text,
    decode_b64_email, merge_contacts,
)
from sellclub import SellClubClient

SITE_ENC = "euc-kr"

SELLCLUB_CRAWL_CATEGORIES = [
    "홍보/마케팅",
    "프로그램/솔루션",
    "교육/강의",
    "IT/개발/보수",
    "디자인/그래픽",
    "유통/무역/생산",
    "입점/제휴/섭외",
    "운영/관리",
    "컨텐츠/제작물",
    "컨설팅/상담",
]

# 목록 행: <a href='../bbs/board.php?bo_table=maket_5_3&wr_id=NNNNNN' title='제목'>...</a>
LIST_LINK_RE = re.compile(
    r"<a[^>]+href=['\"]\.\./bbs/board\.php\?bo_table=maket_5_3&(?:amp;)?wr_id=(\d+)[^'\"]*['\"][^>]*title=['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# 상세에서 작성자 정보 (mb_id, 닉네임, base64 이메일)
WRITER_RE = re.compile(
    r"showSideView\(this,\s*'([^']+)',\s*'([^']+)',\s*'([^']*)'",
)

# 상세 본문 영역
CONTENT_RE = re.compile(
    r"<span id=['\"]ContentsView['\"][^>]*>(.*?)</span>",
    re.DOTALL | re.IGNORECASE,
)

# 카테고리 표시: [홍보/마케팅] 처럼 본문 위에 표시
CATEGORY_RE = re.compile(r"\[([^\[\]<>]{2,30})\]\s*</span>")

# 게시 일시: <span class='v2'>26-05-20 15:22</span>
POSTED_AT_RE = re.compile(
    r"<span\s+class=['\"]v2['\"]>\s*(\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*</span>"
)


class SellClubCrawler(BaseCrawler):
    site_name = "sellclub"

    def __init__(self, client: SellClubClient):
        if not client.logged_in:
            raise RuntimeError("SellClubClient must be logged in")
        self.client = client
        self.session = client.session  # 로그인된 세션 재사용

    def _list_url(self, bo_table: str, page: int, sca: str = "") -> str:
        u = f"{SELLCLUB_BASE}/community/bbs/board.php?bo_table={bo_table}&page={page}"
        if sca:
            from urllib.parse import quote
            u += f"&sca={quote(sca, safe='')}"
        return u

    def _detail_url(self, bo_table: str, wr_id: str) -> str:
        return f"{SELLCLUB_BASE}/community/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}"

    @staticmethod
    def _split_board_spec(spec: str) -> tuple[str, str]:
        if "::" in spec:
            bo_table, category = spec.split("::", 1)
            return bo_table, category
        return spec, ""

    def _get_text(self, url: str) -> str:
        r = self.session.get(url, timeout=self.client.timeout)
        return r.content.decode(SITE_ENC, errors="replace")

    def _parse_list(self, html: str, bo_table: str) -> list[dict]:
        """목록 HTML 에서 (wr_id, title, url) 리스트 추출."""
        seen = set()
        out = []
        for m in LIST_LINK_RE.finditer(html):
            wr_id, title = m.group(1), m.group(2)
            if wr_id in seen:
                continue
            seen.add(wr_id)
            out.append({
                "wr_id": wr_id,
                "title": title.strip(),
                "url": self._detail_url(bo_table, wr_id),
            })
        return out

    def _parse_detail(self, html: str) -> dict:
        """상세 HTML 에서 작성자/카테고리/본문/게시일 + 연락처 추출."""
        data: dict = {
            "writer": "", "writer_email": "", "category": "",
            "posted_at": "", "body": "", "contact": ContactInfo(),
        }

        # 작성자
        wm = WRITER_RE.search(html)
        if wm:
            mb_id = wm.group(1)
            nickname = wm.group(2)
            b64email = wm.group(3)
            data["writer"] = f"{nickname} ({mb_id})"
            if b64email:
                data["writer_email"] = decode_b64_email(b64email)

        # 카테고리
        cm = CATEGORY_RE.search(html)
        if cm:
            cat = cm.group(1).strip()
            # 빈 값이나 너무 일반적인 것 거름
            if cat and cat not in ("", " "):
                data["category"] = cat

        # 게시 일시
        pm = POSTED_AT_RE.search(html)
        if pm:
            data["posted_at"] = pm.group(1).strip()

        # 본문
        bm = CONTENT_RE.search(html)
        if bm:
            body_html = bm.group(1)
            body_text = html_to_text(body_html)
            data["body"] = body_text
            data["contact"] = extract_contacts(body_text)
        # 작성자 이메일 합치기
        if data["writer_email"]:
            meta_contact = ContactInfo(emails=[data["writer_email"]])
            data["contact"] = merge_contacts(data["contact"], meta_contact)

        return data

    def crawl(
        self,
        cfg: CrawlConfig,
        on_log: Callable[[str], None] = print,
        on_lead: Optional[Callable[[Lead], None]] = None,
    ) -> int:
        boards = cfg.boards or ["maket_5_3"]
        new_count = 0

        for spec in boards:
            if should_stop(cfg):
                on_log("[셀클럽] 중지 요청 감지")
                break
            bo, sca = self._split_board_spec(spec)
            empty_streak = 0
            label = f"{bo}/{sca}" if sca else bo
            on_log(f"[셀클럽] 게시판 '{label}' 수집 시작 ({cfg.pages_per_board}페이지)")
            for page in range(1, cfg.pages_per_board + 1):
                if should_stop(cfg):
                    on_log("[셀클럽] 중지 요청 감지")
                    break
                url = self._list_url(bo, page, sca)
                try:
                    html = self._get_text(url)
                except Exception as e:
                    on_log(f"  페이지 {page} GET 실패: {e}")
                    continue

                items = self._parse_list(html, bo)
                on_log(f"  페이지 {page}: 목록 {len(items)}건")
                if not items:
                    empty_streak += 1
                    if empty_streak >= cfg.deep_stop_pages:
                        on_log(f"  연속 빈 페이지 {empty_streak}회 → 조기 종료")
                        break
                    continue

                # 키워드 매칭 (제목)
                page_new = 0
                for it in items:
                    if should_stop(cfg):
                        on_log("[셀클럽] 중지 요청 감지")
                        break
                    matched = matches_keywords(it["title"], cfg.keywords, cfg.keyword_op)
                    if cfg.keywords and cfg.match_in == "title" and not matched:
                        continue

                    # 상세 GET (선택)
                    detail = {}
                    if cfg.fetch_detail:
                        try:
                            sleep_jitter(cfg.detail_delay_min, cfg.detail_delay_max, cfg.stop_event)
                            if should_stop(cfg):
                                break
                            d_html = self._get_text(it["url"])
                            detail = self._parse_detail(d_html)
                        except Exception as e:
                            on_log(f"  상세 {it['wr_id']} 실패: {e}")
                            detail = {}

                    # 키워드를 본문에서도 검사
                    if cfg.keywords and cfg.match_in == "title_or_body":
                        combined = it["title"] + " " + (detail.get("body") or "")
                        matched = matches_keywords(combined, cfg.keywords, cfg.keyword_op)
                        if not matched:
                            continue

                    contact: ContactInfo = detail.get("contact") or ContactInfo()
                    body = detail.get("body", "")
                    excerpt = (body[:200] + "...") if len(body) > 200 else body

                    lead = Lead(
                        site=self.site_name,
                        post_url=it["url"],
                        board=bo,
                        category=detail.get("category", "") or sca,
                        title=it["title"],
                        body_excerpt=excerpt,
                        body_text=body,
                        writer=detail.get("writer", ""),
                        posted_at=detail.get("posted_at", ""),
                        kakao_ids=contact.kakao_ids,
                        open_chats=contact.open_chat_urls,
                        phones=contact.phones,
                        emails=contact.emails,
                        company=contact.company,
                        matched_keywords=matched if cfg.keywords else [],
                    )
                    lead_id = upsert_lead(lead)
                    lead.id = lead_id
                    new_count += 1
                    page_new += 1
                    if on_lead:
                        on_lead(lead)

                if should_stop(cfg):
                    break
                if page_new == 0:
                    empty_streak += 1
                    if empty_streak >= cfg.deep_stop_pages:
                        on_log(f"  연속 매칭 0회 {empty_streak}페이지 → 조기 종료")
                        break
                else:
                    empty_streak = 0

                # 페이지 간 딜레이
                sleep_jitter(cfg.page_delay_min, cfg.page_delay_max, cfg.stop_event)

        on_log(f"[셀클럽] 수집 완료. 누적 신규/갱신 {new_count}건")
        return new_count
