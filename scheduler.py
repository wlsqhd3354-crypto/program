"""반복 발송 스케줄러 (다중사이트 지원).

라운드 단위로 동작:
  - 매 라운드마다 rotator.next() 로 글/이미지 가져옴
  - MultiBot.post_round() 로 활성 사이트 전체에 동시 게시
  - 사이트별 결과를 로그 콜백으로 전달
  - 라운드 사이엔 interval_sec ± jitter_sec 대기
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from content import Rotator
from multibot import MultiBot

LogCallback = Callable[[str], None]


@dataclass
class JobConfig:
    repeat_count: int
    interval_sec: int
    jitter_sec: int = 0


class PostingJob:
    def __init__(
        self,
        bot: MultiBot,
        rotator: Rotator,
        config: JobConfig,
        on_log: LogCallback = print,
    ):
        self.bot = bot
        self.rotator = rotator
        self.config = config
        self.on_log = on_log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sent = 0
        self._failed = 0

    @property
    def sent(self) -> int: return self._sent
    @property
    def failed(self) -> int: return self._failed

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._sent = 0
        self._failed = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        cfg = self.config
        try:
            for i in range(cfg.repeat_count):
                if self._stop.is_set():
                    self.on_log("[중지] 사용자 요청으로 종료")
                    return

                post = self.rotator.next()
                self.on_log(f"━━ 라운드 {i+1}/{cfg.repeat_count} ━━ 글: {post.title}")
                self.on_log(f"  현재 상태: {self.bot.status_line()}")

                results = self.bot.post_round(post.title, post.body, post.images)

                round_ok = 0
                round_fail = 0
                for name, res in results.items():
                    if res.ok:
                        round_ok += 1
                        url = f" → {res.posted_url}" if res.posted_url else ""
                        self.on_log(f"  [{name}] OK ({res.message}){url}")
                    else:
                        round_fail += 1
                        self.on_log(f"  [{name}] FAIL: {res.message}")

                self._sent += round_ok
                self._failed += round_fail

                if i + 1 >= cfg.repeat_count:
                    break

                wait = cfg.interval_sec
                if cfg.jitter_sec > 0:
                    wait += random.randint(-cfg.jitter_sec, cfg.jitter_sec)
                wait = max(5, wait)

                self.on_log(f"  대기 {wait}초...")
                for _ in range(wait):
                    if self._stop.is_set():
                        self.on_log("[중지] 대기 중 종료")
                        return
                    time.sleep(1)
        finally:
            self.on_log(f"[종료] 누적 성공 {self._sent}건 / 실패 {self._failed}건")
