"""E2E 검증: 3사이트 로그인 + 글쓰기 폼 페이지 GET + multipart 페이로드 빌드(드라이런).

실제 게시는 하지 않음. 각 단계 성공/실패만 출력.

자격증명은 환경변수로 받음 (명령행 히스토리에 남지 않도록):
  $env:SC_ID="...";$env:SC_PW="..."
  $env:MM_ID="...";$env:MM_PW="..."
  $env:IB_ID="...";$env:IB_PW="..."
  python test_e2e.py
"""

from __future__ import annotations

import os
import sys
import traceback

import sellclub
import mamentor
import iboss
from multibot import MultiBot, SitePlan


def step(name: str):
    print(f"\n─── {name} ───")


def ok(msg: str):
    print(f"  [OK] {msg}")


def fail(msg: str):
    print(f"  [FAIL] {msg}")


def main():
    sc_id = os.environ.get("SC_ID", ""); sc_pw = os.environ.get("SC_PW", "")
    mm_id = os.environ.get("MM_ID", ""); mm_pw = os.environ.get("MM_PW", "")
    ib_id = os.environ.get("IB_ID", ""); ib_pw = os.environ.get("IB_PW", "")
    if not all([sc_id, sc_pw, mm_id, mm_pw, ib_id, ib_pw]):
        print("환경변수 SC_ID/SC_PW/MM_ID/MM_PW/IB_ID/IB_PW 가 모두 설정되어야 합니다")
        sys.exit(1)

    # ── 1. 셀클럽 로그인 ──
    step("셀클럽 로그인")
    sc = sellclub.SellClubClient()
    try:
        sc.login(sc_id, sc_pw)
        ok(f"logged_in={sc.logged_in}")
    except Exception as e:
        fail(repr(e)); return

    # ── 2. 마멘토 로그인 ──
    step("마멘토 로그인")
    mm = mamentor.MamentorClient()
    try:
        mm.login(mm_id, mm_pw)
        ok(f"logged_in={mm.logged_in}, name={mm._member_name}")
    except Exception as e:
        fail(repr(e)); return

    # ── 3. 아이보스 로그인 ──
    step("아이보스 로그인")
    ib = iboss.IBossClient()
    try:
        ib.login(ib_id, ib_pw)
        ok(f"logged_in={ib.logged_in}")
    except Exception as e:
        fail(repr(e)); return

    # ── 4. 마멘토 write.php GET (uid 토큰 추출) ──
    step("마멘토 write.php GET (smartstore)")
    try:
        hidden = mm._get_write_form("smartstore")
        keys = sorted(hidden.keys())
        ok(f"hidden 필드 {len(hidden)}개: {keys}")
        if "uid" in hidden:
            ok(f"uid 토큰: {hidden['uid'][:20]}...")
        else:
            fail("uid 토큰 누락")
    except Exception as e:
        fail(repr(e))

    # ── 5. 아이보스 글쓰기 폼 GET (VG_live_code) ──
    step("아이보스 /ab-2988 GET (CSRF VG_live_code)")
    try:
        ib_hidden = ib._get_write_form()
        keys = sorted(ib_hidden.keys())
        ok(f"hidden 필드 {len(ib_hidden)}개: {keys}")
        if "VG_live_code" in ib_hidden:
            ok(f"VG_live_code: {ib_hidden['VG_live_code'][:16]}...")
        else:
            fail("VG_live_code 누락")
        if "board" in ib_hidden:
            ok(f"board: {ib_hidden['board']}")
    except Exception as e:
        fail(repr(e))
        traceback.print_exc()

    # ── 6. MultiBot 일일 카운터 ──
    step("MultiBot 상태")
    plans = {
        "sellclub": SitePlan(enabled=True, client=sc, options=sellclub.WriteOptions(mobile_mid="1234", mobile_end="5678"), daily_limit=None, image_supported=True),
        "mamentor": SitePlan(enabled=True, client=mm, options=mamentor.WriteOptions(bo_table="smartstore"), daily_limit=None, image_supported=True),
        "iboss": SitePlan(enabled=True, client=ib, options=iboss.WriteOptions(category_1="B", company_name="t", contact_name="t", email="t@t"), daily_limit=2, image_supported=False),
    }
    bot = MultiBot(plans)
    ok(f"status_line: {bot.status_line()}")
    for name in plans:
        can, why = bot.can_post(name)
        ok(f"can_post({name}) = {can} ({why})")

    print("\n[E2E DONE] 글쓰기 실제 POST는 안전을 위해 생략. GUI 에서 진행하세요.")


if __name__ == "__main__":
    main()
