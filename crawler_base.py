"""크롤러 공통 인터페이스 + 유틸."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event
from typing import Iterable

from db import Lead


@dataclass
class CrawlConfig:
    keywords: list[str]                 # 키워드 (OR 매칭)
    boards: list[str]                   # 사이트별 게시판 코드 (예: 마멘토 ["smartstore","blog_mkt"])
    pages_per_board: int = 3            # 게시판당 페이지 수
    page_delay_min: float = 3.0         # 페이지 사이 최소 대기(초)
    page_delay_max: float = 6.0         # 페이지 사이 최대 대기(초)
    detail_delay_min: float = 1.5       # 상세 GET 사이 최소 대기
    detail_delay_max: float = 3.0       # 상세 GET 사이 최대 대기
    deep_stop_pages: int = 2            # N페이지 연속 신규 0건이면 조기 종료
    fetch_detail: bool = True           # 상세 페이지 GET 여부 (False면 목록 정보만)
    match_in: str = "title_or_body"     # "title" | "title_or_body"
    keyword_op: str = "or"              # "or" | "and"
    stop_event: Event | None = None     # GUI 중지 버튼에서 전달되는 종료 신호


def should_stop(cfg: CrawlConfig) -> bool:
    return bool(cfg.stop_event and cfg.stop_event.is_set())


def sleep_jitter(lo: float, hi: float, stop_event: Event | None = None):
    delay = 0.0
    if hi > lo:
        delay = random.uniform(lo, hi)
    elif lo > 0:
        delay = lo
    if delay <= 0:
        return
    if stop_event:
        stop_event.wait(delay)
    else:
        time.sleep(delay)


def matches_keywords(text: str, keywords: list[str], op: str = "or") -> list[str]:
    """text 안에 어떤 키워드가 매칭됐는지 리스트로 반환."""
    if not keywords:
        return []  # 키워드 없음 = 매칭 검사 안 함 (전체 수집은 호출측에서 결정)
    lower = text.lower()
    compact = "".join(lower.split())
    hits = []
    for k in keywords:
        key = k.lower()
        key_compact = "".join(key.split())
        if key in lower or (key_compact and key_compact in compact):
            hits.append(k)
    if op == "and":
        return hits if len(hits) == len(keywords) else []
    return hits


class BaseCrawler(ABC):
    """모든 사이트 크롤러의 공통 베이스."""

    site_name: str = "?"

    @abstractmethod
    def crawl(self, cfg: CrawlConfig, on_log=print, on_lead=None) -> int:
        """크롤링 실행. 반환: 발견한 신규 lead 수.
        on_log(str): 진행 로그 콜백
        on_lead(Lead): lead 1건 발견할 때마다 호출 (실시간 GUI 업데이트용)
        """
        ...
