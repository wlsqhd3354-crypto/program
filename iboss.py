"""아이보스 HTTP 클라이언트.

- 로그인: POST /member/login_process.php (multipart/form-data)
- 글쓰기: POST /board/article_write.php (multipart/form-data, UTF-8)

특이사항:
- 본문 에디터가 Summernote (HTML)임. comment_1 필드에 HTML 본문 + is_html=Y.
- 동적 CSRF 토큰 VG_live_code 가 /ab-2988 페이지마다 새로 발급됨 → 매번 파싱.
- 이미지: Summernote 의 base64 data URL 임베드 방식 사용 (브라우저에서 이미지를
  복사-붙여넣기 하는 것과 동일). 별도 업로드 엔드포인트 불필요.
- 일일 2회 제한은 MultiBot 레벨에서 관리.
"""

from __future__ import annotations

import re
import base64
import mimetypes
import os
import html as html_lib
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin

import requests

from base import BoardClient, WriteResult
from config import USER_AGENT

# 본문에 임베드할 이미지 1개당 권장 최대 크기 (1MB) — 너무 크면 본문 길이 초과
MAX_EMBED_IMAGE_BYTES = 1_048_576

IBOSS_BASE = "https://www.i-boss.co.kr"
LOGIN_POST_URL = f"{IBOSS_BASE}/member/login_process.php"
WRITE_GET_URL = f"{IBOSS_BASE}/ab-2988"             # 바이럴 서비스 글쓰기 페이지
WRITE_POST_URL = f"{IBOSS_BASE}/board/article_write.php"
BOARD_ID = "BD2986"                                  # 바이럴 서비스 게시판 ID

# 카테고리 (구분) 코드
CATEGORY_OPTIONS = {
    "B": "블로그", "C": "카페", "A": "인스타그램", "D": "유튜브",
    "S": "SNS", "J": "스토어", "U": "언론홍보", "W": "SEO", "F": "지도",
    "G": "포스팅", "E": "체험단", "H": "인플루언서", "O": "숏폼", "N": "PPL",
    "Z": "기타",
}


class IBossError(Exception):
    pass


@dataclass
class WriteOptions:
    """아이보스 바이럴서비스 글쓰기 옵션."""
    category_1: str = "B"          # 구분 코드 (B/C/A/D/S/J/U/W/F/G/E/H/O/N/Z)
    company_name: str = ""         # etc_1 회사명 (필수)
    contact_name: str = ""         # etc_4 담당자명 (필수)
    phone: str = ""                # phone_2 연락처
    email: str = ""                # etc_5 이메일
    nateon: str = ""               # etc_2 네이트온
    kakao: str = ""                # etc_3 카카오톡

    def has_any_contact(self) -> bool:
        return any([self.phone, self.email, self.nateon, self.kakao])


class IBossClient(BoardClient):
    site_name = "iboss"
    supports_images = True  # base64 data URL 임베드 방식

    def __init__(self, timeout: int = 20):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        self.timeout = timeout
        self._user_id: str | None = None

    def login(self, user_id: str, password: str) -> bool:
        # 1) 로그인 페이지 GET (세션 쿠키)
        try:
            self.session.get(f"{IBOSS_BASE}/ab-login", timeout=self.timeout)
        except Exception:
            pass

        # 2) multipart POST
        # iBoss 로그인 폼은 multipart/form-data 임 (특이함). requests files= 사용.
        files = [
            ("after_db_script", (None, b"")),
            ("after_db_msg", (None, b"")),
            ("Q_STRING", (None, b"design_file=login.php")),
            ("user_id", (None, user_id.encode("utf-8"))),
            ("user_passwd", (None, password.encode("utf-8"))),
            ("submit_OK", (None, "로그인".encode("utf-8"))),
        ]
        resp = self.session.post(
            LOGIN_POST_URL,
            files=files,
            headers={"Referer": f"{IBOSS_BASE}/ab-login", "Origin": IBOSS_BASE},
            timeout=self.timeout,
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            raise IBossError(f"HTTP {resp.status_code}")

        # 3) 세션 검증: 메인 페이지에서 로그아웃 링크 확인
        check = self.session.get(IBOSS_BASE + "/", timeout=self.timeout)
        text = check.text
        if "logout_process.php" in text:
            self.logged_in = True
            self._user_id = user_id
            return True

        alert = self.extract_alert(resp.text)
        raise IBossError(f"로그인 실패: {alert or '세션 확인 안 됨'}")

    def _get_write_form(self) -> dict:
        """글쓰기 페이지 GET → 동적 hidden(VG_live_code 등) 파싱."""
        r = self.session.get(WRITE_GET_URL, timeout=self.timeout)
        if r.status_code >= 400:
            raise IBossError(f"write page HTTP {r.status_code}")
        text = r.text
        if "logout_process.php" not in text:
            raise IBossError("세션 만료 - 다시 로그인 필요")

        # 글쓰기 폼 영역만 추출 (사이드바 로그인폼/다른 폼 hidden 제외)
        # form name='TCBOARD_BD2986_WRITE_index...' 부터 </form> 까지
        m = re.search(
            r"<form\s+name='TCBOARD_BD2986_WRITE[^']*'[^>]*>(.*?)</form>",
            text,
            re.DOTALL,
        )
        if not m:
            raise IBossError("글쓰기 폼을 찾을 수 없음")
        form_html = m.group(1)

        hidden = {}
        for mm in re.finditer(
            r"<input\s+type=['\"]?hidden['\"]?\s+name=['\"]?([A-Za-z0-9_]+)['\"]?\s+value=['\"]([^'\"]*)['\"]",
            form_html,
        ):
            hidden[mm.group(1)] = html_lib.unescape(mm.group(2))
        # name 이 뒤에 오는 경우 (value=...name=...)
        for mm in re.finditer(
            r"<input\s+type=['\"]?hidden['\"]?\s+value=['\"]([^'\"]*)['\"]\s+name=['\"]?([A-Za-z0-9_]+)['\"]?",
            form_html,
        ):
            hidden.setdefault(mm.group(2), html_lib.unescape(mm.group(1)))

        # writer_name, email 같은 readonly hidden 필드도 추출
        for mm in re.finditer(
            r"<input\s+type=['\"]?hidden['\"]?\s+name=['\"]?(writer_name|email|phone_2)['\"]?\s+value=['\"]([^'\"]*)['\"]",
            form_html,
        ):
            hidden.setdefault(mm.group(1), html_lib.unescape(mm.group(2)))

        return hidden

    def write_post(
        self,
        title: str,
        content: str,
        options: WriteOptions,
        images: Iterable[str] = (),
    ) -> WriteResult:
        if not self.logged_in:
            raise IBossError("먼저 login() 호출 필요")

        if not options.company_name:
            return WriteResult(False, 0, "", "회사명(company_name) 필수")
        if not options.contact_name:
            return WriteResult(False, 0, "", "담당자명(contact_name) 필수")
        if not options.has_any_contact():
            return WriteResult(False, 0, "", "연락처/이메일/네이트온/카카오톡 중 하나 이상 필수")
        if options.category_1 not in CATEGORY_OPTIONS:
            return WriteResult(False, 0, "", f"잘못된 카테고리: {options.category_1}")

        # 1) 글쓰기 페이지 GET → hidden 파싱 (VG_live_code 등)
        hidden = self._get_write_form()

        # 2) 본문을 HTML 로 변환 (Summernote 는 HTML 으로 받음). 줄바꿈은 <br>.
        body_html = html_lib.escape(content).replace("\n", "<br>")

        # 2-1) 이미지를 base64 data URL 로 본문 끝에 임베드
        #     (브라우저에서 이미지 복사-붙여넣기와 동일한 방식)
        image_paths = list(images or [])
        embedded = []
        for path in image_paths:
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            if size > MAX_EMBED_IMAGE_BYTES:
                # 너무 크면 건너뜀 (본문 길이 초과 위험)
                continue
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            embedded.append(f'<p><img src="data:{mime};base64,{b64}" /></p>')
        if embedded:
            body_html = body_html + "<p><br></p>" + "".join(embedded)

        # 3) multipart 필드 구성
        fields: list[tuple[str, tuple]] = []
        # hidden 들
        for k, v in hidden.items():
            fields.append((k, (None, v.encode("utf-8"))))
        # 필수 ID 채우기 (혹시 hidden 에서 누락된 게 있어도 보강)
        fields.append(("board", (None, BOARD_ID.encode("utf-8"))))
        fields.append(("Q_STRING", (None, b"design_file=2988.php")))
        fields.append(("flag", (None, b"i-boss.co.kr")))
        fields.append(("P_SELF", (None, b"insiter.php")))
        # 작성자/제목/카테고리/연락처
        fields.append(("subject", (None, title.encode("utf-8"))))
        fields.append(("category_1", (None, options.category_1.encode("utf-8"))))
        fields.append(("etc_1", (None, options.company_name.encode("utf-8"))))
        fields.append(("etc_4", (None, options.contact_name.encode("utf-8"))))
        fields.append(("phone_2", (None, options.phone.encode("utf-8"))))
        fields.append(("etc_5", (None, options.email.encode("utf-8"))))
        fields.append(("etc_2", (None, options.nateon.encode("utf-8"))))
        fields.append(("etc_3", (None, options.kakao.encode("utf-8"))))
        fields.append(("relation_serial_1", (None, b"")))
        fields.append(("relation_table_1", (None, b"")))
        # 본문 (HTML)
        fields.append(("comment_1", (None, body_html.encode("utf-8"))))
        fields.append(("is_html", (None, b"Y")))

        # 4) POST
        resp = self.session.post(
            WRITE_POST_URL,
            files=fields,
            headers={"Referer": WRITE_GET_URL, "Origin": IBOSS_BASE},
            timeout=self.timeout,
            allow_redirects=True,
        )

        text = resp.text
        if resp.status_code >= 400:
            return WriteResult(False, resp.status_code, resp.url, f"HTTP {resp.status_code}")

        alert = self.extract_alert(text)
        if alert:
            return WriteResult(False, resp.status_code, resp.url, alert)

        meta_refresh = re.search(r"url=([^'\">\s]+)", text, re.IGNORECASE)
        if meta_refresh:
            posted_url = urljoin(resp.url, meta_refresh.group(1))
            if "/ab-2987-" in posted_url or "/ab-2986" in posted_url:
                return WriteResult(True, resp.status_code, resp.url, "등록 완료", posted_url=posted_url)

        # 성공: after_db_script 가 %MOVE%../ab-2987-{serial} 로 처리되어 view 페이지로 redirect
        if "/ab-2987-" in resp.url:
            return WriteResult(True, resp.status_code, resp.url, "등록 완료", posted_url=resp.url)
        if "/ab-2986" in resp.url:
            return WriteResult(True, resp.status_code, resp.url, "등록 완료", posted_url=resp.url)
        return WriteResult(True, resp.status_code, resp.url, "응답 확인 필요")
