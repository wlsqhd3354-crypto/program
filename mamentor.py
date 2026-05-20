"""마멘토 HTTP 클라이언트.

- 로그인: POST /bbs/login_check.php
- 글쓰기: POST /bbs/write_update.php (multipart/form-data, UTF-8)

자유홍보광고 카테고리에는 30+개의 sub-board가 있고 사용자가 하나 선택.
write.php?bo_table=XXX 페이지에는 동적 'uid' 토큰이 있어서 매번 파싱해야 함.
"""

from __future__ import annotations

import os
import re
import mimetypes
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import quote

import requests

from base import BoardClient, WriteResult
from config import USER_AGENT

MAMENTOR_BASE = "https://mamentor.co.kr"
LOGIN_POST_URL = f"{MAMENTOR_BASE}/bbs/login_check.php"
WRITE_POST_URL = f"{MAMENTOR_BASE}/bbs/write_update.php"
WRITE_TOKEN_URL = f"{MAMENTOR_BASE}/bbs/write_token.php"

# 자유홍보광고 하위 게시판 목록 (bo_table → 표시명)
FREE_AD_BOARDS = {
    "smartstore": "스마트스토어",
    "s_reware": "쇼핑리워드",
    "coupang": "쿠팡",
    "openmarcket": "기타오픈마켓",
    "closemarcket": "기타폐쇄몰",
    "program": "프로그램 판매",
    "programbuy": "프로그램 구매",
    "keyword_ad": "키워드광고",
    "place_ad": "플레이스리워드",
    "autocomplete": "자완",
    "blog_mkt": "블로그 마케팅",
    "cafe_mkt": "카페 마케팅",
    "etc_search_mkt": "기타 검색마케팅",
    "coin": "플레이스배포",
    "facebook": "페이스북",
    "instagram": "인스타그램",
    "youtube": "유튜브",
    "etc_sns": "기타 SNS",
    "media_mkt": "언론마케팅",
    "CPA_CPS": "CPA/CPS",
    "app_mkt": "앱 마케팅",
    "etc_online_mkt": "기타 온라인마케팅",
    "class": "마케팅 강좌",
    "aietc": "AI마케팅",
    "offline_mkt": "오프라인 마케팅",
    "mkt_story": "마케팅이야기",
    "mkt_beginner": "마케팅 왕초보",
    "etc_mkt": "기타",
    "xiaohongshu": "왕홍,샤오홍슈",
    "chinaetc": "중국마케팅기타",
}


class MamentorError(Exception):
    pass


@dataclass
class WriteOptions:
    """마멘토 글쓰기 옵션."""
    bo_table: str = "smartstore"   # 자유홍보광고 하위 게시판
    ca_name: str = ""              # 카테고리 (게시판마다 다를 수 있음, 보통 게시판명과 동일)
    link1: str = ""
    link2: str = ""


class MamentorClient(BoardClient):
    site_name = "mamentor"
    supports_images = True

    def __init__(self, timeout: int = 20):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        self.timeout = timeout
        self._member_name: str | None = None

    def login(self, mb_id: str, mb_password: str) -> bool:
        # 1) 로그인 페이지 GET (세션 쿠키)
        try:
            self.session.get(f"{MAMENTOR_BASE}/bbs/login.php", timeout=self.timeout)
        except Exception:
            pass

        # 2) 로그인 POST (UTF-8)
        data = {
            "url": f"{MAMENTOR_BASE}",
            "mb_id": mb_id,
            "mb_password": mb_password,
        }
        resp = self.session.post(
            LOGIN_POST_URL,
            data=data,
            timeout=self.timeout,
            allow_redirects=True,
            headers={
                "Referer": f"{MAMENTOR_BASE}/bbs/login.php",
                "Origin": MAMENTOR_BASE,
            },
        )
        if resp.status_code >= 400:
            raise MamentorError(f"HTTP {resp.status_code}")

        text = resp.text
        alert = self.extract_alert(text)
        if alert and ("실패" in alert or "확인" in alert or "잘못" in alert or "없는" in alert):
            raise MamentorError(f"로그인 실패: {alert}")

        # 3) 검증: 메인 페이지에서 로그아웃 링크 또는 mb_id 가 보이는지
        if ("logout.php" in text) or ("로그아웃" in text) or (mb_id in text):
            self.logged_in = True
            # 별명 추출 (선택사항)
            m = re.search(r">([^<]+)님 로그인 중", text)
            if m:
                self._member_name = m.group(1).strip()
            return True

        check = self.session.get(MAMENTOR_BASE + "/", timeout=self.timeout)
        text2 = check.text
        if ("logout.php" in text2) or ("로그아웃" in text2) or (mb_id in text2):
            self.logged_in = True
            return True

        raise MamentorError(f"로그인 실패: {alert or '세션 확인 안 됨'}")

    def _get_write_form(self, bo_table: str) -> dict:
        """write.php 페이지 GET 후 동적 hidden 필드(uid 등) 추출.

        세션 만료 판정은 fwrite 폼 존재 여부로 함.
        ("로그인 후"는 페이지 내 다른 텍스트로도 등장할 수 있어 false-positive)
        """
        url = f"{MAMENTOR_BASE}/bbs/write.php?bo_table={bo_table}"
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code >= 400:
            raise MamentorError(f"write.php HTTP {r.status_code}")
        text = r.text

        # URL 이 login.php 로 redirect 됐으면 세션 만료
        if "/bbs/login.php" in r.url:
            raise MamentorError("write.php 접근 거부 - 로그인 페이지로 리다이렉트됨")

        # fwrite 폼만 추출 (사이드바/다른 폼 hidden 제외)
        form_m = re.search(
            r'<form\s+name=["\']fwrite["\'][^>]*>(.*?)</form>',
            text,
            re.DOTALL,
        )
        if not form_m:
            raise MamentorError("fwrite 폼을 찾을 수 없음 (세션 만료 또는 게시판 권한 없음)")
        form_html = form_m.group(1)

        hidden = {}
        for m in re.finditer(
            r'<input\s+type=["\']hidden["\']\s+name=["\']([^"\']+)["\']\s+value=["\']([^"\']*)["\']',
            form_html,
        ):
            hidden[m.group(1)] = m.group(2)
        for m in re.finditer(
            r'<input\s+type=["\']hidden["\']\s+value=["\']([^"\']*)["\']\s+name=["\']([^"\']+)["\']',
            form_html,
        ):
            hidden.setdefault(m.group(2), m.group(1))
        return hidden

    def write_post(
        self,
        title: str,
        content: str,
        options: WriteOptions,
        images: Iterable[str] = (),
    ) -> WriteResult:
        if not self.logged_in:
            raise MamentorError("먼저 login() 호출 필요")

        # 1) write.php GET → hidden 필드들 (uid 포함) 파싱
        hidden = self._get_write_form(options.bo_table)

        token_resp = self.session.post(
            WRITE_TOKEN_URL,
            data={"bo_table": options.bo_table},
            headers={
                "Referer": f"{MAMENTOR_BASE}/bbs/write.php?bo_table={options.bo_table}",
                "Origin": MAMENTOR_BASE,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.timeout,
        )
        try:
            token_data = token_resp.json()
        except ValueError as e:
            raise MamentorError("write token response parse failed") from e
        if token_resp.status_code >= 400 or token_data.get("error"):
            raise MamentorError(token_data.get("error") or f"write token HTTP {token_resp.status_code}")
        token = token_data.get("token")
        if not token:
            raise MamentorError("write token missing")
        hidden["token"] = token

        # 2) 필수 hidden 기본값 보강
        hidden.setdefault("w", "")
        hidden.setdefault("bo_table", options.bo_table)
        hidden.setdefault("wr_id", "0")
        hidden.setdefault("sca", "")
        hidden.setdefault("sfl", "")
        hidden.setdefault("stx", "")
        hidden.setdefault("spt", "")
        hidden.setdefault("sst", "")
        hidden.setdefault("sod", "")
        hidden.setdefault("page", "")
        hidden.setdefault("html", "html1")

        # 3) 폼 필드 (UTF-8, multipart)
        ca = options.ca_name or FREE_AD_BOARDS.get(options.bo_table, "")

        fields: list[tuple[str, tuple]] = []
        for k, v in hidden.items():
            fields.append((k, (None, v.encode("utf-8"))))
        fields.append(("ca_name", (None, ca.encode("utf-8"))))
        fields.append(("wr_subject", (None, title.encode("utf-8"))))
        fields.append(("wr_content", (None, content.encode("utf-8"))))
        fields.append(("wr_link1", (None, options.link1.encode("utf-8"))))
        fields.append(("wr_link2", (None, options.link2.encode("utf-8"))))

        image_paths = list(images)
        if not image_paths:
            fields.append(("bf_file[]", ("", b"", "application/octet-stream")))
        else:
            for path in image_paths:
                if not os.path.isfile(path):
                    continue
                size = os.path.getsize(path)
                if size > 1_048_576:
                    raise MamentorError(
                        f"이미지 1MB 제한 초과: {os.path.basename(path)} ({size:,} bytes)"
                    )
                fname = os.path.basename(path)
                mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                with open(path, "rb") as f:
                    data = f.read()
                fields.append(("bf_file[]", (fname, data, mime)))

        # 4) POST
        write_get_url = f"{MAMENTOR_BASE}/bbs/write.php?bo_table={options.bo_table}"
        resp = self.session.post(
            WRITE_POST_URL,
            files=fields,
            headers={"Referer": write_get_url, "Origin": MAMENTOR_BASE},
            timeout=self.timeout,
            allow_redirects=True,
        )

        text = resp.text
        if resp.status_code >= 400:
            return WriteResult(False, resp.status_code, resp.url, f"HTTP {resp.status_code}")

        # 성공: board.php 나 view 페이지로 redirect
        posted = ""
        if "board.php" in resp.url and f"bo_table={options.bo_table}" in resp.url:
            posted = resp.url
            return WriteResult(True, resp.status_code, resp.url, "등록 완료", posted_url=posted)
        if "&wr_id=" in resp.url:
            return WriteResult(True, resp.status_code, resp.url, "등록 완료", posted_url=resp.url)

        alert = self.extract_alert(text)
        if alert:
            return WriteResult(False, resp.status_code, resp.url, alert)

        return WriteResult(True, resp.status_code, resp.url, "응답 확인 필요")
