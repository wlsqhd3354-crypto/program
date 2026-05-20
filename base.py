"""사이트 공통 인터페이스. 셀클럽/마멘토/아이보스가 모두 구현."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable


@dataclass
class WriteResult:
    ok: bool
    status_code: int
    final_url: str
    message: str = ""
    posted_url: str = ""  # 등록 성공 시 게시물 URL (가능하면)


class BoardClient(ABC):
    """모든 사이트 클라이언트의 공통 인터페이스."""

    site_name: str = "?"
    supports_images: bool = True

    def __init__(self):
        self.logged_in: bool = False

    @abstractmethod
    def login(self, user_id: str, password: str) -> bool:
        ...

    @abstractmethod
    def write_post(
        self,
        title: str,
        content: str,
        options,
        images: Iterable[str] = (),
    ) -> WriteResult:
        ...

    @staticmethod
    def extract_alert(html: str) -> str:
        m = re.search(r"alert\(['\"](.+?)['\"]\)", html)
        return m.group(1) if m else ""
