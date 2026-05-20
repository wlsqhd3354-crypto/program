"""크롤러 공통 인터페이스 + 유틸."""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
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


def sleep_jitter(lo: float, hi: float):
    if hi > lo:
        time.sleep(random.uniform(lo, hi))
    elif lo > 0:
        time.sleep(lo)


def matches_keywords(text: str, keywords: list[str], op: str = "or") -> list[str]:
    """text 안에 어떤 키워드가 매칭됐는지 리스트로 반환."""
    if not keywords:
        return []  # 키워드 없음 = 매칭 검사 안 함 (전체 수집은 호출측에서 결정)
    lower = text.lower()
    hits = [k for k in keywords if k.lower() in lower]
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
