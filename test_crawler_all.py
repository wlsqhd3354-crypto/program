"""3사이트 통합 크롤러 PoC. 각 사이트 1페이지씩, 키워드 없이 전체 수집.

환경변수: SC_ID / SC_PW / MM_ID / MM_PW / IB_ID / IB_PW
"""

from __future__ import annotations

import os
import sys

from db import init_db, stats, get_leads
from crawler_base import CrawlConfig
from sellclub import SellClubClient
from mamentor import MamentorClient
from iboss import IBossClient
from sellclub_crawler import SellClubCrawler
from mamentor_crawler import MamentorCrawler
from iboss_crawler import IBossCrawler
from crawler_runner import CrawlJob
import time


def main():
    sc_id = os.environ.get("SC_ID"); sc_pw = os.environ.get("SC_PW")
    mm_id = os.environ.get("MM_ID"); mm_pw = os.environ.get("MM_PW")
    ib_id = os.environ.get("IB_ID"); ib_pw = os.environ.get("IB_PW")
    if not all([sc_id, sc_pw, mm_id, mm_pw, ib_id, ib_pw]):
        print("환경변수 SC_ID/SC_PW/MM_ID/MM_PW/IB_ID/IB_PW 모두 설정 필요")
        sys.exit(1)

    init_db()

    print("[1/3] 셀클럽 로그인"); sc = SellClubClient(); sc.login(sc_id, sc_pw); print("  OK")
    print("[2/3] 마멘토 로그인"); mm = MamentorClient(); mm.login(mm_id, mm_pw); print("  OK")
    print("[3/3] 아이보스 로그인"); ib = IBossClient(); ib.login(ib_id, ib_pw); print("  OK")

    common = dict(
        keywords=[],          # 빈 키워드 = 전체 수집
        pages_per_board=1,
        page_delay_min=2.0, page_delay_max=4.0,
        detail_delay_min=1.0, detail_delay_max=2.5,
        deep_stop_pages=2,
        fetch_detail=True,
        match_in="title_or_body", keyword_op="or",
    )

    crawlers = [
        SellClubCrawler(sc),
        MamentorCrawler(mm),
        IBossCrawler(ib),
    ]
    cfg_map = {
        "sellclub": CrawlConfig(boards=["maket_5_3"], **common),
        "mamentor": CrawlConfig(boards=["smartstore"], **common),
        "iboss":    CrawlConfig(boards=[], **common),   # 전체 카테고리
    }

    done_flag = [False]
    def on_done(counts):
        print(f"[완료] counts={counts}")
        done_flag[0] = True

    job = CrawlJob(crawlers, cfg_map, on_log=print, on_done=on_done)
    job.start()
    while not done_flag[0]:
        time.sleep(0.5)

    s = stats()
    def safe(s):
        """CP949 콘솔 호환: 한국어 표시 못하는 문자 → ?"""
        if not s: return ""
        return str(s).encode("cp949", errors="replace").decode("cp949")

    print(f"\n[전체 DB] {s}")
    # 사이트별 추출률 통계
    print("\n--- 추출률 통계 ---")
    for site in ("sellclub", "mamentor", "iboss"):
        leads = get_leads(site=site, limit=1000)
        total = len(leads)
        with_kakao = sum(1 for L in leads if L.kakao_ids or L.open_chats)
        with_phone = sum(1 for L in leads if L.phones)
        with_email = sum(1 for L in leads if L.emails)
        with_any = sum(1 for L in leads if L.kakao_ids or L.open_chats or L.phones or L.emails)
        print(f"  [{site}] total={total}  카톡={with_kakao}({with_kakao*100//max(total,1)}%) "
              f"전화={with_phone}({with_phone*100//max(total,1)}%) "
              f"이메일={with_email}({with_email*100//max(total,1)}%) "
              f"연락가능={with_any}({with_any*100//max(total,1)}%)")
    print("\n--- 사이트별 샘플 ---")
    for site in ("sellclub", "mamentor", "iboss"):
        leads = get_leads(site=site, limit=3)
        print(f"\n[{site}] {len(leads)}건 샘플:")
        for L in leads:
            kakao_count = len(L.kakao_ids) + len(L.open_chats)
            phone_count = len(L.phones); email_count = len(L.emails)
            print(safe(f"  [{L.id}] {L.title[:60]}"))
            print(safe(f"        회사={L.company} 카톡={kakao_count} 전화={phone_count} 이메일={email_count}"))


if __name__ == "__main__":
    main()
