"""크롤러 실행 매니저 (스레드 + 콜백).

GUI 에서 크롤링 시작/중지/진행상황을 다루기 위한 wrapper.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from crawler_base import CrawlConfig
from db import Lead


LogCb = Callable[[str], None]
LeadCb = Callable[[Lead], None]


class CrawlJob:
    def __init__(
        self,
        crawlers: list,                # 인스턴스 리스트 (SellClubCrawler / MamentorCrawler / IBossCrawler)
        cfg_per_site: dict,            # {"sellclub": CrawlConfig, "mamentor": CrawlConfig, ...}
        on_log: LogCb = print,
        on_lead: Optional[LeadCb] = None,
        on_done: Optional[Callable[[dict], None]] = None,
    ):
        self.crawlers = crawlers
        self.cfg_per_site = cfg_per_site
        self.on_log = on_log
        self.on_lead = on_lead
        self.on_done = on_done
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._counts: dict[str, int] = {}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._counts = {}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            for cr in self.crawlers:
                if self._stop.is_set():
                    self.on_log("[중지] 사용자 요청"); break
                site = cr.site_name
                cfg = self.cfg_per_site.get(site)
                if not cfg:
                    self.on_log(f"[{site}] 설정 없음 - 건너뜀"); continue
                self.on_log(f"━━ {site} 크롤링 시작 ━━")
                try:
                    n = cr.crawl(cfg, on_log=self.on_log, on_lead=self.on_lead)
                    self._counts[site] = n
                except Exception as e:
                    self.on_log(f"[{site}] 크롤링 예외: {e!r}")
                    self._counts[site] = 0
        finally:
            self.on_log(f"[완료] 사이트별 신규/갱신: {self._counts}")
            if self.on_done:
                self.on_done(self._counts)
