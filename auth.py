"""구글 시트에 등록된 사용자만 프로그램을 실행할 수 있도록 검증.

시트 구조: A열=ID, B열=PW (헤더 없음).
시트는 '링크가 있는 모든 사용자: 뷰어'로 공개되어 있어야 함.
"""

import csv
import io
import requests

from config import GSHEET_CSV_URL, USER_AGENT


class AuthError(Exception):
    pass


def fetch_users(timeout: int = 10) -> list[tuple[str, str]]:
    """구글시트 CSV를 받아 (id, pw) 목록으로 반환."""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(GSHEET_CSV_URL, headers=headers, timeout=timeout, allow_redirects=True)
    if resp.status_code != 200:
        raise AuthError(f"시트를 불러올 수 없습니다 (HTTP {resp.status_code})")

    text = resp.content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))

    users: list[tuple[str, str]] = []
    for row in reader:
        if len(row) < 2:
            continue
        uid, pw = row[0].strip(), row[1].strip()
        if uid and pw:
            users.append((uid, pw))
    return users


def verify(user_id: str, password: str) -> bool:
    """사용자가 시트에 등록되어 있는지 확인."""
    if not user_id or not password:
        return False
    try:
        users = fetch_users()
    except Exception as e:
        raise AuthError(f"인증 서버 통신 실패: {e}")
    return (user_id.strip(), password.strip()) in users


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: python auth.py <id> <pw>")
        sys.exit(1)
    print("OK" if verify(sys.argv[1], sys.argv[2]) else "FAIL")
