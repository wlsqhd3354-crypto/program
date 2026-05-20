"""실 게시 검증: 한 사이트에 테스트글 1건 등록.

사용법 (PowerShell):
  $env:SC_ID="..."; $env:SC_PW="..."
  python test_post.py sellclub

  $env:MM_ID="..."; $env:MM_PW="..."
  python test_post.py mamentor

  $env:IB_ID="..."; $env:IB_PW="..."
  python test_post.py iboss

테스트 후 게시판에서 직접 삭제해야 함.
"""

from __future__ import annotations

import os
import sys
import struct
import zlib
from pathlib import Path

import sellclub
import mamentor
import iboss


def make_tiny_png(path: str):
    """1x1 빨간 픽셀 PNG (~100 bytes). 사이트 이미지 첨부 검증용."""
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(typ: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(typ + data)
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1 RGB
    raw = b"\x00\xff\x00\x00"  # filter byte + red pixel
    idat = zlib.compress(raw)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("sellclub", "mamentor", "iboss"):
        print("usage: python test_post.py <sellclub|mamentor|iboss>")
        sys.exit(1)
    site = sys.argv[1]

    # 테스트 이미지 준비
    img_path = "_test_image.png"
    make_tiny_png(img_path)
    print(f"[준비] 테스트 이미지 생성: {img_path} ({os.path.getsize(img_path)} bytes)")

    title = "[테스트] 자동발송기 동작확인 - 즉시 삭제 예정"
    body = (
        "이 글은 자동발송기 동작 검증용 테스트 게시물입니다.\n"
        "확인 후 즉시 삭제됩니다.\n"
        "(시스템 점검을 위한 일회성 등록)"
    )

    if site == "sellclub":
        uid = os.environ.get("SC_ID"); pw = os.environ.get("SC_PW")
        if not uid or not pw:
            print("환경변수 SC_ID / SC_PW 설정 필요"); sys.exit(1)
        c = sellclub.SellClubClient()
        c.login(uid, pw)
        print(f"[로그인] OK")
        opts = sellclub.WriteOptions(
            category="홍보/마케팅",
            deal_status="on",
            reg_class="대행합니다",
            deal_method="쪽지연락",
            post_type="3",
            phone_area="02", phone_mid="0000", phone_end="0000",
            mobile_area="010", mobile_mid="0000", mobile_end="0000",
        )
        print(f"[발송] 셀클럽 등록 시도 (포인트 -1500P 차감 예정)...")
        res = c.write_post(title, body, opts, [img_path])
        print(f"  ok={res.ok}, msg={res.message}, http={res.status_code}")
        print(f"  final_url={res.final_url}")
        if res.posted_url: print(f"  posted_url={res.posted_url}")

    elif site == "mamentor":
        uid = os.environ.get("MM_ID"); pw = os.environ.get("MM_PW")
        if not uid or not pw:
            print("환경변수 MM_ID / MM_PW 설정 필요"); sys.exit(1)
        c = mamentor.MamentorClient()
        c.login(uid, pw)
        print(f"[로그인] OK")
        opts = mamentor.WriteOptions(bo_table="smartstore", ca_name="스마트스토어")
        print(f"[발송] 마멘토 등록 시도 (자유홍보광고 > 스마트스토어)...")
        res = c.write_post(title, body, opts, [img_path])
        print(f"  ok={res.ok}, msg={res.message}, http={res.status_code}")
        print(f"  final_url={res.final_url}")
        if res.posted_url: print(f"  posted_url={res.posted_url}")

    elif site == "iboss":
        uid = os.environ.get("IB_ID"); pw = os.environ.get("IB_PW")
        if not uid or not pw:
            print("환경변수 IB_ID / IB_PW 설정 필요"); sys.exit(1)
        c = iboss.IBossClient()
        c.login(uid, pw)
        print(f"[로그인] OK")
        opts = iboss.WriteOptions(
            category_1="B",
            company_name="테스트",
            contact_name="테스트",
            phone="010-0000-0000",
            email="test@example.com",
        )
        print(f"[발송] 아이보스 등록 시도 (바이럴서비스, 포인트/결제 부족 예상)...")
        res = c.write_post(title, body, opts, [img_path])
        print(f"  ok={res.ok}, msg={res.message}, http={res.status_code}")
        print(f"  final_url={res.final_url}")
        if res.posted_url: print(f"  posted_url={res.posted_url}")

    # 임시 이미지 삭제
    try: os.remove(img_path)
    except: pass


if __name__ == "__main__":
    main()
