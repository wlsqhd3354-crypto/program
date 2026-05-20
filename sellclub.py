"""셀클럽 HTTP 클라이언트.

- 로그인: POST /community/bbs/login_check2.php?login=1
- 글쓰기: POST /community/bbs/write_update.php (multipart/form-data, EUC-KR)

사이트가 EUC-KR을 쓰기 때문에 한글은 반드시 EUC-KR로 인코딩해서 보내야 함.
requests 기본동작은 UTF-8이라 직접 바이트로 변환.
"""

from __future__ import annotations

import os
import re
import mimetypes
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin
from typing import Iterable

import requests

from base import BoardClient, WriteResult
from config import (
    SELLCLUB_BASE,
    SELLCLUB_BOARD,
    USER_AGENT,
)

LOGIN_POST_URL = f"{SELLCLUB_BASE}/community/bbs/login_check2.php?login=1"
WRITE_GET_URL = f"{SELLCLUB_BASE}/community/bbs/write.php?bo_table={SELLCLUB_BOARD}"
WRITE_POST_URL = f"{SELLCLUB_BASE}/community/bbs/write_update.php"
SITE_ENC = "euc-kr"


class SellClubError(Exception):
    pass


@dataclass
class WriteOptions:
    """글쓰기 시 폼 필드. 한 번 설정해두면 매 게시물에 재사용."""
    category: str = "홍보/마케팅"        # ca_name (필수)
    deal_status: str = "on"              # wr_9 (on=거래가능, off=거래종료)
    reg_class: str = "대행합니다"        # ext6_00 (대행합니다 / 의뢰받아요)
    deal_method: str = "쪽지연락"        # ext6_01
    post_type: str = "3"                 # wr_1 (3=기본, 2=굵게, 1=급등, 0=추천)
    phone_area: str = "02"               # ext5_00
    phone_mid: str = ""                  # ext5_01
    phone_end: str = ""                  # ext5_02
    mobile_area: str = "010"             # ext5_03
    mobile_mid: str = ""                 # ext5_04
    mobile_end: str = ""                 # ext5_05
    link1: str = ""
    link2: str = ""


def _enc(value: str) -> bytes:
    """폼 필드 문자열을 EUC-KR 바이트로 인코딩 (사이트 charset에 맞춤)."""
    return value.encode(SITE_ENC, errors="replace")


class SellClubClient(BoardClient):
    site_name = "sellclub"
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
        """셀클럽 로그인. 성공 시 True, 실패 시 SellClubError.

        검증은 로그인 후 게시판 페이지(board.php)에 접근해
        헤더에 'logout.php' 링크 또는 사용자 mb_id 가 보이는지로 판단.
        (login.php 는 로그인 상태와 무관하게 항상 로그인 폼을 보여주므로 검증에 부적합)
        """
        # 1) 먼저 login.php GET 으로 세션 쿠키 확보
        try:
            self.session.get(
                f"{SELLCLUB_BASE}/community/bbs/login.php",
                timeout=self.timeout,
            )
        except Exception:
            pass

        # 2) 로그인 POST
        # 사이트 hidden input 의 value 그대로 (브라우저는 이걸 그대로 form-urlencode 함)
        data = {
            "url": "%2Fcommunity%2Fbbs%2Flogin.php",
            "memhack": "ok2",
            "mb_id": mb_id,
            "mb_password": mb_password,
        }
        resp = self.session.post(
            LOGIN_POST_URL,
            data=data,
            timeout=self.timeout,
            allow_redirects=True,
            headers={
                "Referer": f"{SELLCLUB_BASE}/community/bbs/login.php",
                "Content-Type": f"application/x-www-form-urlencoded; charset={SITE_ENC}",
                "Origin": SELLCLUB_BASE,
            },
        )

        if resp.status_code >= 400:
            raise SellClubError(f"로그인 HTTP 오류: {resp.status_code}")

        # POST 응답에 alert 가 있으면 즉시 실패
        post_text = resp.content.decode(SITE_ENC, errors="replace")
        alert = self._extract_alert(post_text)
        if alert and ("실패" in alert or "확인" in alert or "잘못" in alert or "없는" in alert):
            raise SellClubError(f"로그인 실패: {alert}")

        # 3) 검증: 게시판 페이지에서 로그인 헤더(로그아웃 링크, mb_id) 확인
        check = self.session.get(
            f"{SELLCLUB_BASE}/community/bbs/board.php?bo_table={SELLCLUB_BOARD}",
            timeout=self.timeout,
            headers={"Referer": SELLCLUB_BASE + "/community/"},
        )
        text = check.content.decode(SITE_ENC, errors="replace")
        if ("logout.php" in text) or ("로그아웃" in text) or (mb_id in text):
            self.logged_in = True
            return True

        # 메인 페이지로 한 번 더 시도 (theme 차이 대비)
        check2 = self.session.get(SELLCLUB_BASE + "/community/", timeout=self.timeout)
        text2 = check2.content.decode(SITE_ENC, errors="replace")
        if ("logout.php" in text2) or ("로그아웃" in text2) or (mb_id in text2):
            self.logged_in = True
            return True

        raise SellClubError(f"로그인 실패: {alert or '세션 확인 안 됨 (아이디/비밀번호 또는 봇 차단 가능성)'}")

    @staticmethod
    def _extract_alert(html: str) -> str:
        import re
        m = re.search(r"alert\(['\"](.+?)['\"]\)", html)
        return m.group(1) if m else ""

    def write_post(
        self,
        title: str,
        content: str,
        options: WriteOptions,
        images: Iterable[str] = (),
    ) -> WriteResult:
        """게시판에 글 등록.

        title/content/options 의 한글 필드는 EUC-KR 로 인코딩됨.
        images: 파일 경로 리스트. 셀클럽 제한 1,048,576 bytes(1MB).
        """
        if not self.logged_in:
            raise SellClubError("먼저 login() 호출 필요")

        # write.php 페이지를 먼저 GET (referer & 세션 갱신)
        try:
            self.session.get(WRITE_GET_URL, timeout=self.timeout)
        except Exception:
            pass

        # 폼 필드 구성
        fields: list[tuple[str, bytes]] = [
            ("w", b""),
            ("bo_table", _enc(SELLCLUB_BOARD)),
            ("wr_id", b""),
            ("sfl", b""), ("stx", b""), ("spt", b""),
            ("sst", b""), ("sod", b""), ("page", b""),
            ("wr_1", _enc(options.post_type)),
            ("ca_name", _enc(options.category)),
            ("wr_9", _enc(options.deal_status)),
            ("ext6_00", _enc(options.reg_class)),
            ("ext6_01", _enc(options.deal_method)),
            ("ext5_00", _enc(options.phone_area)),
            ("ext5_01", _enc(options.phone_mid)),
            ("ext5_02", _enc(options.phone_end)),
            ("ext5_03", _enc(options.mobile_area)),
            ("ext5_04", _enc(options.mobile_mid)),
            ("ext5_05", _enc(options.mobile_end)),
            ("wr_subject", _enc(title)),
            ("wr_content", _enc(content)),
            ("wr_link1", _enc(options.link1)),
            ("wr_link2", _enc(options.link2)),
            ("wr_2", b"1"),
        ]

        # multipart 빌드 — requests에 files 로 텍스트 필드도 같이 보내면
        # multipart/form-data 로 자동 생성됨. 인코딩 문제로 직접 처리.
        # 텍스트 필드는 (None, bytes, None) 튜플로 넘기면 plain text 파트로 들어감.
        multipart: list[tuple[str, tuple]] = []
        for name, value in fields:
            multipart.append((name, (None, value)))

        # 이미지 파일들
        image_paths = list(images)
        if not image_paths:
            multipart.append(("bf_file[]", ("", b"", "application/octet-stream")))
        else:
            for path in image_paths:
                if not os.path.isfile(path):
                    continue
                size = os.path.getsize(path)
                if size > 1_048_576:
                    raise SellClubError(
                        f"이미지가 1MB 제한 초과: {os.path.basename(path)} ({size:,} bytes)"
                    )
                fname = os.path.basename(path)
                mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
                with open(path, "rb") as f:
                    data = f.read()
                multipart.append(("bf_file[]", (fname, data, mime)))

        headers = {
            "Referer": WRITE_GET_URL,
        }

        resp = self.session.post(
            WRITE_POST_URL,
            files=multipart,
            headers=headers,
            timeout=self.timeout,
            allow_redirects=True,
        )

        text = resp.content.decode(SITE_ENC, errors="replace")

        # 그누보드는 성공 시 board.php 로 redirect 되고, 실패 시 alert 후 history.back()
        if resp.status_code >= 400:
            return WriteResult(False, resp.status_code, resp.url, f"HTTP {resp.status_code}")

        alert = self._extract_alert(text)
        if alert:
            return WriteResult(False, resp.status_code, resp.url, alert)

        redirect = re.search(r"location\.replace\(['\"](.+?)['\"]\)", text)
        if redirect:
            posted_url = urljoin(resp.url, redirect.group(1))
            return WriteResult(True, resp.status_code, resp.url, "등록 완료", posted_url=posted_url)

        # 성공 판정: 최종 URL 이 board.php 거나 view.php 면 OK
        if "board.php" in resp.url or "view.php" in resp.url:
            return WriteResult(True, resp.status_code, resp.url, "등록 완료")

        # 그 외엔 일단 성공으로 보고하되 메시지 첨부
        return WriteResult(True, resp.status_code, resp.url, "응답 확인 필요")
