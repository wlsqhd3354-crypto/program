"""마멘토 크롤러 (그누보드5).

목록: /bbs/board.php?bo_table={BOARD}&page=N
상세: /bbs/board.php?bo_table={BOARD}&wr_id=NNN

비로그인도 글 본문은 보이지만, 일부 게시판은 회원 전용 → MamentorClient 세션 사용 권장.
"""

from __future__ import annotations

import re
from typing import Callable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from crawler_base import BaseCrawler, CrawlConfig, sleep_jitter, matches_keywords, should_stop
from db import Lead, upsert_lead
from extractor import (
    ContactInfo, extract_contacts, html_to_text, extract_min_price,
)
from mamentor import MamentorClient, MAMENTOR_BASE

# 상세: 제목
TITLE_RE = re.compile(
    r'<span\s+class=["\']bo_v_tit["\']>\s*(.+?)\s*</span>',
    re.DOTALL,
)
# 상세: 작성일
DATE_RE = re.compile(
    r'<strong\s+class=["\']if_date["\']>\s*<span[^>]*>작성일</span>([0-9.\-/\s:]+)</strong>'
)
# 상세: 작성자 닉네임 + mb_id
WRITER_RE = re.compile(
    r'<a\s+href=["\']https?://mamentor\.co\.kr/bbs/profile\.php\?mb_id=([^"\']+)["\']\s+class=["\']sv_member["\'][^>]*>(?:<[^>]+>)*\s*([^<\s][^<]*?)\s*</a>',
)

class MamentorCrawler(BaseCrawler):
    site_name = "mamentor"

    def __init__(self, client: MamentorClient):
        if not client.logged_in:
            raise RuntimeError("MamentorClient must be logged in")
        self.client = client
        self.session = client.session

    def _list_url(self, bo: str, page: int) -> str:
        return f"{MAMENTOR_BASE}/bbs/board.php?bo_table={bo}&page={page}"

    def _detail_url(self, bo: str, wr_id: str) -> str:
        return f"{MAMENTOR_BASE}/bbs/board.php?bo_table={bo}&wr_id={wr_id}"

    def _get(self, url: str) -> str:
        r = self.session.get(url, timeout=self.client.timeout)
        return r.text

    def _parse_list(self, html: str, bo_filter: str) -> list[dict]:
        """목록에서 (bo, wr_id, title) 추출. bo_filter 와 같은 게시판만."""
        seen = set()
        items = []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href*="board.php"][href*="wr_id="]'):
            if self._is_sidebar_link(a):
                continue
            href = urljoin(MAMENTOR_BASE, a.get("href", ""))
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            bo = (qs.get("bo_table") or [""])[0]
            wr_id = (qs.get("wr_id") or [""])[0]
            if bo != bo_filter:
                continue
            if not wr_id or wr_id in seen:
                continue
            seen.add(wr_id)
            # 제목에서 댓글 count 등 노이즈 제거 (목록의 a 안에 댓글 [n] span 들어가있음)
            title = a.get("title") or a.get_text(" ", strip=True)
            title = re.sub(r"\s+", " ", title).strip()
            if not title:
                continue
            items.append({
                "bo": bo,
                "wr_id": wr_id,
                "title": title,
                "url": self._detail_url(bo, wr_id),
            })
        return items

    @staticmethod
    def _is_sidebar_link(anchor) -> bool:
        """인기글/최신글 사이드 영역 링크는 게시판 목록으로 보지 않는다."""
        blocked_ids = {"hit_con_box", "ol_after", "side_4tabs"}
        blocked_classes = {"hit_list", "new_articles", "latest_wr", "side_4tabs"}
        for parent in anchor.parents:
            parent_id = parent.get("id")
            if parent_id in blocked_ids:
                return True
            classes = set(parent.get("class") or [])
            if classes & blocked_classes:
                return True
        return False

    def _parse_detail(self, html: str) -> dict:
        data = {"title": "", "writer": "", "posted_at": "", "body": "", "contact": ContactInfo()}

        tm = TITLE_RE.search(html)
        if tm:
            data["title"] = html_to_text(tm.group(1)).strip()

        dm = DATE_RE.search(html)
        if dm:
            data["posted_at"] = dm.group(1).strip()

        wm = WRITER_RE.search(html)
        if wm:
            data["writer"] = f"{wm.group(2).strip()} ({wm.group(1)})"

        soup = BeautifulSoup(html, "html.parser")
        body_node = soup.find(id="bo_v_con")
        if body_node:
            body_text = html_to_text(str(body_node))
            data["body"] = body_text
            data["contact"] = extract_contacts(body_text)

        return data

    def crawl(
        self,
        cfg: CrawlConfig,
        on_log: Callable[[str], None] = print,
        on_lead: Optional[Callable[[Lead], None]] = None,
    ) -> int:
        boards = cfg.boards or ["smartstore"]
        new_count = 0

        for bo in boards:
            if should_stop(cfg):
                on_log("[마멘토] 중지 요청 감지")
                break
            empty_streak = 0
            on_log(f"[마멘토] 게시판 '{bo}' 수집 시작 ({cfg.pages_per_board}페이지)")
            for page in range(1, cfg.pages_per_board + 1):
                if should_stop(cfg):
                    on_log("[마멘토] 중지 요청 감지")
                    break
                url = self._list_url(bo, page)
                try:
                    html = self._get(url)
                except Exception as e:
                    on_log(f"  페이지 {page} GET 실패: {e}")
                    continue

                items = self._parse_list(html, bo)
                on_log(f"  페이지 {page}: 목록 {len(items)}건")
                if not items:
                    empty_streak += 1
                    if empty_streak >= cfg.deep_stop_pages:
                        on_log(f"  빈 페이지 {empty_streak}회 → 조기 종료")
                        break
                    continue

                page_new = 0
                for it in items:
                    if should_stop(cfg):
                        on_log("[마멘토] 중지 요청 감지")
                        break
                    matched = matches_keywords(it["title"], cfg.keywords, cfg.keyword_op)
                    if cfg.keywords and cfg.match_in == "title" and not matched:
                        continue

                    detail = {}
                    if cfg.fetch_detail:
                        try:
                            sleep_jitter(cfg.detail_delay_min, cfg.detail_delay_max, cfg.stop_event)
                            if should_stop(cfg):
                                break
                            detail = self._parse_detail(self._get(it["url"]))
                        except Exception as e:
                            on_log(f"  상세 {it['wr_id']} 실패: {e}")
                            detail = {}

                    if cfg.keywords and cfg.match_in == "title_or_body":
                        combined = it["title"] + " " + (detail.get("body") or "")
                        matched = matches_keywords(combined, cfg.keywords, cfg.keyword_op)
                        if not matched:
                            continue

                    contact: ContactInfo = detail.get("contact") or ContactInfo()
                    body = detail.get("body", "")
                    excerpt = (body[:200] + "...") if len(body) > 200 else body
                    title = detail.get("title") or it["title"]
                    min_price, price_text = extract_min_price(f"{title}\n{body}", cfg.keywords)

                    lead = Lead(
                        site=self.site_name,
                        post_url=it["url"],
                        board=bo,
                        title=title,
                        body_excerpt=excerpt,
                        body_text=body,
                        writer=detail.get("writer", ""),
                        posted_at=detail.get("posted_at", ""),
                        kakao_ids=contact.kakao_ids,
                        open_chats=contact.open_chat_urls,
                        phones=contact.phones,
                        emails=contact.emails,
                        company=contact.company,
                        min_price=min_price,
                        price_text=price_text,
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

                sleep_jitter(cfg.page_delay_min, cfg.page_delay_max, cfg.stop_event)

        on_log(f"[마멘토] 수집 완료. 누적 신규/갱신 {new_count}건")
        return new_count
