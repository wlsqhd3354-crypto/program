"""다중사이트 오케스트레이터.

한 라운드에 활성화된 모든 사이트에 같은 글/이미지를 게시.
사이트별 일일 한도(iBoss=2, 그 외 무제한) 자동 관리.
일일 카운터는 daily_state.json 에 저장되어 자정에 자동 초기화.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from base import BoardClient, WriteResult
from paths import resource_path

DAILY_STATE_FILE = "daily_state.json"

# 사이트별 일일 최대치 (None = 무제한, 또는 사용자 설정값)
DEFAULT_DAILY_LIMITS = {
    "sellclub": None,
    "mamentor": None,
    "iboss": 2,
}


@dataclass
class SitePlan:
    """사이트 하나의 발송 계획."""
    enabled: bool = True
    client: BoardClient = None  # type: ignore
    options: object = None       # 사이트별 WriteOptions
    daily_limit: int | None = None  # None=무제한
    image_supported: bool = True
    label: str = ""


def _load_state() -> dict:
    path = resource_path(DAILY_STATE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(s: dict):
    path = resource_path(DAILY_STATE_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class MultiBot:
    """3개 사이트 통합 발송기."""

    def __init__(self, plans: dict[str, SitePlan]):
        self.plans = plans  # {"sellclub": SitePlan, "mamentor": ..., "iboss": ...}
        self._refresh_daily_state()

    def _refresh_daily_state(self):
        s = _load_state()
        today = date.today().isoformat()
        if s.get("date") != today:
            s = {"date": today, "counts": {}}
            _save_state(s)
        self._state = s

    def _today_count(self, name: str) -> int:
        self._refresh_daily_state()
        return self._state.get("counts", {}).get(name, 0)

    def _increment_count(self, name: str):
        self._refresh_daily_state()
        self._state.setdefault("counts", {})
        self._state["counts"][name] = self._state["counts"].get(name, 0) + 1
        _save_state(self._state)

    def can_post(self, name: str) -> tuple[bool, str]:
        """오늘 더 게시 가능한지. (가능여부, 이유)."""
        plan = self.plans.get(name)
        if not plan or not plan.enabled:
            return False, "사이트 비활성"
        if not plan.client or not plan.client.logged_in:
            return False, "로그인 안 됨"
        if plan.daily_limit is not None:
            done = self._today_count(name)
            if done >= plan.daily_limit:
                return False, f"일일 한도 도달 ({done}/{plan.daily_limit})"
        return True, "ok"

    def plan_label(self, name: str) -> str:
        plan = self.plans.get(name)
        return (plan.label if plan and plan.label else name)

    def post_round(
        self,
        title: str,
        content: str,
        images: list[str],
    ) -> dict[str, WriteResult]:
        """현재 라운드: 활성화된 모든 사이트에 같은 글/이미지 게시."""
        results: dict[str, WriteResult] = {}
        for name, plan in self.plans.items():
            ok, reason = self.can_post(name)
            if not ok:
                results[name] = WriteResult(False, 0, "", f"건너뜀: {reason}")
                continue

            # 이미지 미지원 사이트는 텍스트만
            imgs = images if plan.image_supported else []

            try:
                res = plan.client.write_post(
                    title=title,
                    content=content,
                    options=plan.options,
                    images=imgs,
                )
                results[name] = res
                if res.ok:
                    self._increment_count(name)
            except Exception as e:
                results[name] = WriteResult(False, 0, "", f"예외: {e!r}")
        return results

    def status_line(self) -> str:
        """현재 사이트별 오늘 카운트/한도 요약."""
        parts = []
        for name, plan in self.plans.items():
            if not plan.enabled:
                parts.append(f"{self.plan_label(name)}: OFF")
                continue
            cnt = self._today_count(name)
            lim = plan.daily_limit if plan.daily_limit is not None else "∞"
            parts.append(f"{self.plan_label(name)}: {cnt}/{lim}")
        return " | ".join(parts)
