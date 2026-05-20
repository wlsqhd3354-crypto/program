"""sellclub.py 로그인 디버그 v2 - board.php 검증까지."""
import sys
from sellclub import SellClubClient, SellClubError, SITE_ENC, SELLCLUB_BASE, SELLCLUB_BOARD

if len(sys.argv) != 3:
    print("usage: python test_login.py <id> <pw>")
    sys.exit(1)

mb_id, mb_pw = sys.argv[1], sys.argv[2]
c = SellClubClient()
try:
    c.login(mb_id, mb_pw)
    print("[OK] LOGIN OK")
    print("쿠키들:", {k: v[:30]+"..." if len(v)>30 else v for k,v in c.session.cookies.items()})
except SellClubError as e:
    print("[FAIL] LOGIN FAIL:", e)
    print()
    print("--- 디버그: 쿠키 상태 ---")
    print("쿠키들:", {k: v[:30]+"..." if len(v)>30 else v for k,v in c.session.cookies.items()})

    # board.php 직접 확인
    r = c.session.get(f"{SELLCLUB_BASE}/community/bbs/board.php?bo_table={SELLCLUB_BOARD}", timeout=15)
    txt = r.content.decode(SITE_ENC, errors="replace")
    print(f"--- board.php HTTP {r.status_code}, len={len(txt)} ---")
    print(f"'logout.php' in body: {'logout.php' in txt}")
    print(f"'로그아웃' in body: {'로그아웃' in txt}")
    print(f"'{mb_id}' in body: {mb_id in txt}")
    print(f"'바이럴천재' in body: {'바이럴천재' in txt}")
    print(f"'mb_id=' in body: {'mb_id=' in txt}")
    # 본문 일부 (header navi 부분, 보통 1000-3000 line 사이)
    import re
    nav = re.search(r"(로그|logout)[^<]{0,80}", txt)
    if nav:
        print(f"매칭 일부: {nav.group(0)[:120]!r}")
    else:
        print("로그/logout 키워드 없음")
    # 첫 1500자
    print(f"--- 본문 0-1500자 ---\n{txt[:1500]}")
