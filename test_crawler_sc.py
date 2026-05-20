"""셀클럽 크롤러 PoC.

환경변수: SC_ID / SC_PW
키워드 옵션: 인자로 받음 (없으면 전체 수집)

사용:
  $env:SC_ID="..."; $env:SC_PW="..."
  python test_crawler_sc.py            # 1페이지 전체
  python test_crawler_sc.py 블로그 마케팅  # 키워드 매칭만
"""

from __future__ import annotations

import os
import sys

from db import init_db, stats, get_leads
from crawler_base import CrawlConfig
from sellclub import SellClubClient
from sellclub_crawler import SellClubCrawler


def main():
    sc_id = os.environ.get("SC_ID"); sc_pw = os.environ.get("SC_PW")
    if not sc_id or not sc_pw:
        print("환경변수 SC_ID / SC_PW 설정 필요"); sys.exit(1)

    keywords = sys.argv[1:]

    init_db()
    print(f"[로그인] 셀클럽 ...")
    c = SellClubClient()
    c.login(sc_id, sc_pw)
    print(f"[로그인] OK")

    crawler = SellClubCrawler(c)
    cfg = CrawlConfig(
        keywords=keywords,
        boards=["maket_5_3"],   # 대행합니다
        pages_per_board=1,
        page_delay_min=2.0,
        page_delay_max=4.0,
        detail_delay_min=1.5,
        detail_delay_max=3.0,
        fetch_detail=True,
        match_in="title_or_body",
        keyword_op="or",
    )
    if keywords:
        print(f"[크롤링] 키워드: {keywords} (OR), 1페이지")
    else:
        print(f"[크롤링] 전체 수집, 1페이지")
    n = crawler.crawl(cfg, on_log=print)

    print(f"\n[결과] 신규 + 갱신: {n}건")
    print(f"[DB stats] {stats()}")

    # 샘플 5건 출력
    leads = get_leads(site="sellclub", limit=5)
    print(f"\n--- 최근 5건 ---")
    for L in leads:
        print(f"  [{L.id}] {L.title}")
        print(f"      카톡={L.kakao_ids} 오픈챗={L.open_chats} 전화={L.phones} 이메일={L.emails}")
        if L.matched_keywords:
            print(f"      매칭: {L.matched_keywords}")


if __name__ == "__main__":
    main()
