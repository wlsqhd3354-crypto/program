"""아이보스 「바이럴 서비스」(BD2986) 크롤러.

목록: /ab-2986?page=N
상세: /ab-2987-{serial}

상세 페이지에 업체명/담당자명/연락처/카톡/이메일/네이트온이
contact 테이블에 그대로 노출되어 있어 정규식 추출 쉬움.
"""

from __future__ import annotations

import re
from typing import Callable, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from crawler_base import BaseCrawler, CrawlConfig, sleep_jitter, matches_keywords, should_stop
from db import Lead, upsert_lead
from extractor import (
    ContactInfo, extract_contacts, html_to_text, merge_contacts, normalize_phone, extract_min_price,
)
from iboss import IBossClient, IBOSS_BASE, BOARD_ID

# 상세 제목
TITLE_RE = re.compile(
    r'<h1\s+class=["\']main_title["\']>\s*(.+?)\s*</h1>',
    re.DOTALL,
)
# 작성일
DATE_RE = re.compile(
    r'<div\s+class=["\']ABA-tit-box["\']>\s*<p>\s*([0-9.\-/\s:]+)\s*</p>'
)
# 작성자
WRITER_RE = re.compile(
    r'<span[^>]*class=["\']user_tit["\'][^>]*>\s*([^<]+?)\s*</span>'
)

class IBossCrawler(BaseCrawler):
    site_name = "iboss"

    def __init__(self, client: IBossClient):
        if not client.logged_in:
            raise RuntimeError("IBossClient must be logged in")
        self.client = client
        self.session = client.session

    def _list_url(
        self,
        page: int,
        category_1: str = "",
        keyword: str = "",
        search_item: str = "",
    ) -> str:
        if keyword:
            params = {
                "design_file": "2986.php",
                "board": BOARD_ID,
                "search_item": search_item or "subject",
                "search_value": keyword,
                "submit_OK": "검색",
            }
            if page > 1:
                params["PB_1388626082"] = page
            if category_1:
                params["category_1"] = category_1
            return f"{IBOSS_BASE}/ab-2986?{urlencode(params)}"

        u = f"{IBOSS_BASE}/ab-2986?page={page}"
        if category_1:
            u += f"&category_1={category_1}"
        return u

    def _detail_url(self, serial: str) -> str:
        return f"{IBOSS_BASE}/ab-2987-{serial}"

    def _get(self, url: str) -> str:
        r = self.session.get(url, timeout=self.client.timeout)
        return r.text

    def _parse_list(self, html: str) -> list[dict]:
        seen = set()
        items = []
        soup = BeautifulSoup(html, "html.parser")
        scope = soup.find(
            lambda tag: tag.name == "form"
            and "TCBOARD_BD2986_LIST" in (tag.get("id") or tag.get("name") or "")
        ) or soup
        for a in scope.select('a[href*="ab-2987-"]'):
            if not a.find_parent(class_="mb_subject") or self._is_sidebar_link(a):
                continue
            href = a.get("href", "")
            m = re.search(r"ab-2987-(\d+)", href)
            if not m:
                continue
            serial = m.group(1)
            if serial in seen:
                continue
            seen.add(serial)
            title = a.get("title") or a.get_text(" ", strip=True)
            title = re.sub(r"\s+", " ", title).strip()
            if not title:
                continue
            items.append({
                "serial": serial,
                "title": title,
                "url": self._detail_url(serial),
            })
        return items

    @staticmethod
    def _search_items(cfg: CrawlConfig) -> list[str]:
        if cfg.match_in == "title":
            return ["subject"]
        return ["subject", "comment_text_1"]

    @staticmethod
    def _is_sidebar_link(anchor) -> bool:
        blocked_ids = {"side_4tabs"}
        blocked_classes = {"new_articles", "latest_wr", "side_4tabs"}
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
        soup = BeautifulSoup(html, "html.parser")

        tm = TITLE_RE.search(html)
        if tm:
            data["title"] = html_to_text(tm.group(1)).strip()

        dm = DATE_RE.search(html)
        if dm:
            data["posted_at"] = dm.group(1).strip()

        wm = WRITER_RE.search(html)
        if wm:
            data["writer"] = wm.group(1).strip()

        # 메타 테이블 (contact) 한 번에 다 추출. 사이드바/하단 테이블이 섞이지 않도록
        # 상세의 contact 테이블만 대상으로 삼는다.
        meta = {}
        contact_table = soup.find("table", class_="contact")
        if contact_table:
            for row in contact_table.find_all("tr"):
                cells = row.find_all(["th", "td"], recursive=False)
                for idx, cell in enumerate(cells[:-1]):
                    if cell.name != "th" or cells[idx + 1].name != "td":
                        continue
                    label = cell.get_text(" ", strip=True).replace(" ", "")
                    value = cells[idx + 1].get_text(" ", strip=True)
                    if value:
                        meta[label] = value

        info = ContactInfo()
        if "업체명" in meta:
            info.company = meta["업체명"]
        if "카카오톡" in meta:
            v = meta["카카오톡"]
            if v.startswith("http"):
                info.open_chat_urls.append(v)
            else:
                info.kakao_ids.append(v)
        if "연락처" in meta:
            info.phones.append(normalize_phone(meta["연락처"]))
        if "이메일" in meta:
            info.emails.append(meta["이메일"])
        # 네이트온은 별도 필드 없어서 카톡ID에 라벨로 합치지 않고 무시 (필요시 확장)

        # 본문에서도 추가 추출 (테이블에 없는 경우 보완)
        body_node = soup.find(
            lambda tag: tag.name == "div" and "ABA-article-contents" in (tag.get("class") or [])
        )
        if body_node:
            body_text = html_to_text(str(body_node))
            data["body"] = body_text
            body_info = extract_contacts(body_text)
            info = merge_contacts(info, body_info)

        data["contact"] = info
        return data

    def crawl(
        self,
        cfg: CrawlConfig,
        on_log: Callable[[str], None] = print,
        on_lead: Optional[Callable[[Lead], None]] = None,
    ) -> int:
        # 아이보스는 게시판 하나(BD2986)지만 카테고리(B/C/A/... 코드) 로 필터 가능
        # cfg.boards 가 비었으면 전체, 값이 있으면 카테고리 코드들로 처리
        categories = cfg.boards or [""]   # "" = 전체
        new_count = 0
        seen_urls: set[str] = set()

        for cat in categories:
            if should_stop(cfg):
                on_log("[아이보스] 중지 요청 감지")
                break
            label = cat or "전체"
            search_terms = cfg.keywords or [""]
            search_items = self._search_items(cfg) if cfg.keywords else [""]
            for keyword in search_terms:
                for search_item in search_items:
                    empty_streak = 0
                    suffix = f" / 키워드 '{keyword}'" if keyword else ""
                    on_log(f"[아이보스] 카테고리 '{label}' 수집 시작{suffix} ({cfg.pages_per_board}페이지)")
                    for page in range(1, cfg.pages_per_board + 1):
                        if should_stop(cfg):
                            on_log("[아이보스] 중지 요청 감지")
                            break
                        url = self._list_url(page, cat, keyword, search_item)
                        try:
                            html = self._get(url)
                        except Exception as e:
                            on_log(f"  페이지 {page} GET 실패: {e}")
                            continue

                        items = self._parse_list(html)
                        on_log(f"  페이지 {page}: 목록 {len(items)}건")
                        if not items:
                            empty_streak += 1
                            if empty_streak >= cfg.deep_stop_pages:
                                on_log(f"  빈 페이지 {empty_streak}회 → 조기 종료")
                                break
                            continue

                        page_new = 0
                        active_keywords = [keyword] if keyword else cfg.keywords
                        for it in items:
                            if should_stop(cfg):
                                on_log("[아이보스] 중지 요청 감지")
                                break
                            if it["url"] in seen_urls:
                                continue
                            matched = matches_keywords(it["title"], active_keywords, "or")
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
                                    on_log(f"  상세 {it['serial']} 실패: {e}")
                                    detail = {}

                            if cfg.keywords and cfg.match_in == "title_or_body":
                                combined = it["title"] + " " + (detail.get("body") or "")
                                matched = matches_keywords(combined, active_keywords, "or")
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
                                board=BOARD_ID,
                                category=cat,
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
                            seen_urls.add(it["url"])
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

        on_log(f"[아이보스] 수집 완료. 누적 신규/갱신 {new_count}건")
        return new_count
