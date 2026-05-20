"""셀클럽 + 마멘토 + 아이보스 통합 자동발송기 GUI."""

from __future__ import annotations

import os
import sys
import json
import re
import shutil
import threading
import time
import customtkinter as ctk
from tkinter import messagebox, filedialog

import auth
import updater
from content import load_messages, list_images, Rotator
from paths import resource_path
from base import BoardClient
import sellclub
import mamentor
import iboss
from multibot import MultiBot, SitePlan, DEFAULT_DAILY_LIMITS
from scheduler import PostingJob, JobConfig
from config import (
    DEFAULT_INTERVAL_SEC,
    DEFAULT_REPEAT_COUNT,
    MESSAGES_DIR,
    IMAGES_DIR,
    APP_VERSION,
)

# 영업 크롤러
from db import (
    init_db as crawler_init_db,
    get_leads, get_lead, update_status, add_contact, get_contacts, stats as crawler_stats,
    Contact, STATUS_OPTIONS, CHANNEL_OPTIONS, RESULT_OPTIONS,
)
from crawler_base import CrawlConfig
from sellclub_crawler import SellClubCrawler, SELLCLUB_CRAWL_CATEGORIES
from mamentor_crawler import MamentorCrawler
from iboss_crawler import IBossCrawler
from crawler_runner import CrawlJob
from excel_export import export_leads
from datetime import datetime

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SETTINGS_PATH = "settings.json"


def load_settings() -> dict:
    path = resource_path(SETTINGS_PATH)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict):
    path = resource_path(SETTINGS_PATH)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class LoginWindow(ctk.CTk):
    """프로그램 사용 권한 인증."""
    def __init__(self):
        super().__init__()
        self.title("로그인")
        self.geometry("360x240")
        self.resizable(False, False)
        self.authenticated = False
        settings = load_settings()

        ctk.CTkLabel(self, text="자동발송기 (셀클럽+마멘토+아이보스)", font=("Pretendard", 15, "bold")).pack(pady=(20, 4))
        ctk.CTkLabel(self, text="등록된 사용자만 이용 가능", font=("Pretendard", 10), text_color="#888").pack()
        self.id_entry = ctk.CTkEntry(self, placeholder_text="아이디", width=240)
        self.id_entry.pack(pady=(16, 6))
        self.id_entry.insert(0, settings.get("user_id", ""))
        self.pw_entry = ctk.CTkEntry(self, placeholder_text="비밀번호", width=240, show="*")
        self.pw_entry.pack(pady=4)
        self.status = ctk.CTkLabel(self, text="", text_color="#e57373")
        self.status.pack(pady=4)
        ctk.CTkButton(self, text="로그인", width=240, command=self._login).pack(pady=6)
        self.id_entry.focus()
        self.bind("<Return>", lambda _: self._login())

    def _login(self):
        uid, pw = self.id_entry.get().strip(), self.pw_entry.get().strip()
        if not uid or not pw:
            self.status.configure(text="아이디/비밀번호를 입력하세요"); return
        self.status.configure(text="확인 중...", text_color="#888"); self.update()
        try:
            ok = auth.verify(uid, pw)
        except auth.AuthError as e:
            self.status.configure(text=str(e), text_color="#e57373"); return
        if not ok:
            self.status.configure(text="등록되지 않은 사용자입니다", text_color="#e57373"); return
        save_settings({**load_settings(), "user_id": uid})
        self.authenticated = True
        self.destroy()


class MainApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("자동발송기 - 셀클럽/마멘토/아이보스 통합")
        self.geometry("960x780")

        self.sellclub_client: sellclub.SellClubClient | None = None
        self.mamentor_client: mamentor.MamentorClient | None = None
        self.iboss_client: iboss.IBossClient | None = None
        self.job: PostingJob | None = None

        # 크롤러 잡 & DB
        crawler_init_db()
        self.crawl_job: CrawlJob | None = None
        self.auto_scheduler_stop = threading.Event()
        self.auto_scheduler_thread: threading.Thread | None = None

        self._build_ui()
        self._load_saved()
        self._refresh_resources()
        self.after(800, self._check_update_async)

    # ---------- UI 구축 ----------
    def _build_ui(self):
        # 탭뷰: 사이트별 설정
        self.tabs = ctk.CTkTabview(self, height=440)
        self.tabs.pack(fill="x", padx=12, pady=(12, 6))
        self.tabs.add("셀클럽")
        self.tabs.add("마멘토")
        self.tabs.add("아이보스")
        self.tabs.add("공통")
        self.tabs.add("영업크롤러")

        self._build_sellclub_tab(self.tabs.tab("셀클럽"))
        self._build_mamentor_tab(self.tabs.tab("마멘토"))
        self._build_iboss_tab(self.tabs.tab("아이보스"))
        self._build_common_tab(self.tabs.tab("공통"))
        self._build_crawler_tab(self.tabs.tab("영업크롤러"))

        # 컨트롤 영역
        ctrl = ctk.CTkFrame(self)
        ctrl.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(ctrl, text="반복 횟수").grid(row=0, column=0, padx=6)
        self.repeat_count = ctk.CTkEntry(ctrl, width=80); self.repeat_count.insert(0, str(DEFAULT_REPEAT_COUNT)); self.repeat_count.grid(row=0, column=1)
        ctk.CTkLabel(ctrl, text="간격(초)").grid(row=0, column=2, padx=6)
        self.interval = ctk.CTkEntry(ctrl, width=80); self.interval.insert(0, str(DEFAULT_INTERVAL_SEC)); self.interval.grid(row=0, column=3)
        ctk.CTkLabel(ctrl, text="±랜덤(초)").grid(row=0, column=4, padx=6)
        self.jitter = ctk.CTkEntry(ctrl, width=80); self.jitter.insert(0, "0"); self.jitter.grid(row=0, column=5)
        self.start_btn = ctk.CTkButton(ctrl, text="발송 시작", width=100, command=self._start_job)
        self.start_btn.grid(row=0, column=6, padx=10)
        self.stop_btn = ctk.CTkButton(ctrl, text="중지", width=80, fg_color="#9c2c2c", state="disabled", command=self._stop_job)
        self.stop_btn.grid(row=0, column=7, padx=4)
        ctk.CTkLabel(ctrl, text="단독 발송").grid(row=1, column=0, padx=6, pady=(6, 2), sticky="e")
        self.start_sc_only_btn = ctk.CTkButton(ctrl, text="셀클럽만", width=90, command=lambda: self._start_job({"sellclub"}))
        self.start_sc_only_btn.grid(row=1, column=1, padx=4, pady=(6, 2), sticky="w")
        self.start_mm_only_btn = ctk.CTkButton(ctrl, text="마멘토만", width=90, command=lambda: self._start_job({"mamentor"}))
        self.start_mm_only_btn.grid(row=1, column=2, padx=4, pady=(6, 2), sticky="w")
        self.start_ib_only_btn = ctk.CTkButton(ctrl, text="아이보스만", width=90, command=lambda: self._start_job({"iboss"}))
        self.start_ib_only_btn.grid(row=1, column=3, padx=4, pady=(6, 2), sticky="w")

        # 로그
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        ctk.CTkLabel(log_frame, text="로그", font=("Pretendard", 12, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
        self.log = ctk.CTkTextbox(log_frame, font=("Consolas", 11))
        self.log.pack(fill="both", expand=True, padx=10, pady=8)

    def _build_sellclub_tab(self, parent):
        self.sc_enabled = ctk.CTkCheckBox(parent, text="셀클럽 활성화"); self.sc_enabled.select()
        self.sc_enabled.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 4), sticky="w")

        ctk.CTkLabel(parent, text="아이디").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        self.sc_id = ctk.CTkEntry(parent, width=200); self.sc_id.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(parent, text="비밀번호").grid(row=1, column=2, padx=10, sticky="e")
        self.sc_pw = ctk.CTkEntry(parent, width=200, show="*"); self.sc_pw.grid(row=1, column=3, sticky="w")
        self.sc_login_btn = ctk.CTkButton(parent, text="로그인", width=80, command=self._sc_login); self.sc_login_btn.grid(row=1, column=4, padx=10)
        self.sc_status = ctk.CTkLabel(parent, text="미접속", text_color="#888"); self.sc_status.grid(row=1, column=5, sticky="w")

        ctk.CTkLabel(parent, text="카테고리").grid(row=2, column=0, padx=10, pady=4, sticky="e")
        self.sc_category = ctk.CTkOptionMenu(parent, values=[
            "홍보/마케팅", "프로그램/솔루션", "교육/강의", "IT/개발/보수",
            "디자인/그래픽", "유통/무역/생산", "입점/제휴/섭외",
            "운영/관리", "컨텐츠/제작물", "컨설팅/상담",
        ]); self.sc_category.grid(row=2, column=1, sticky="w")

        ctk.CTkLabel(parent, text="등록분류").grid(row=2, column=2, padx=10, sticky="e")
        self.sc_reg = ctk.CTkOptionMenu(parent, values=["대행합니다", "의뢰받아요"]); self.sc_reg.grid(row=2, column=3, sticky="w")

        ctk.CTkLabel(parent, text="거래상황").grid(row=3, column=0, padx=10, pady=4, sticky="e")
        self.sc_dealstatus = ctk.CTkOptionMenu(parent, values=["on", "off"]); self.sc_dealstatus.grid(row=3, column=1, sticky="w")

        ctk.CTkLabel(parent, text="거래방식").grid(row=3, column=2, padx=10, sticky="e")
        self.sc_dealmethod = ctk.CTkEntry(parent, width=200); self.sc_dealmethod.insert(0, "쪽지연락"); self.sc_dealmethod.grid(row=3, column=3, sticky="w")

        ctk.CTkLabel(parent, text="게시물형태").grid(row=4, column=0, padx=10, pady=4, sticky="e")
        self.sc_posttype = ctk.CTkOptionMenu(parent, values=[
            "3 (기본 -1500P)", "2 (굵게 -2000P)", "1 (급등 -2500P)", "0 (추천 -151500P)",
        ]); self.sc_posttype.grid(row=4, column=1, sticky="w")

        pf = ctk.CTkFrame(parent, fg_color="transparent")
        pf.grid(row=5, column=0, columnspan=6, padx=10, pady=4, sticky="w")
        ctk.CTkLabel(pf, text="전화").pack(side="left")
        self.sc_phone_area = ctk.CTkOptionMenu(pf, values=["02","031","032","033","041","042","043","051","052","053","054","055","061","062","063","064","070","0505"], width=80); self.sc_phone_area.pack(side="left", padx=4)
        self.sc_phone_mid = ctk.CTkEntry(pf, width=60); self.sc_phone_mid.pack(side="left", padx=2)
        ctk.CTkLabel(pf, text="-").pack(side="left")
        self.sc_phone_end = ctk.CTkEntry(pf, width=60); self.sc_phone_end.pack(side="left", padx=2)
        ctk.CTkLabel(pf, text="     핸드폰").pack(side="left")
        self.sc_mobile_area = ctk.CTkOptionMenu(pf, values=["010","011","016","017","018","019"], width=80); self.sc_mobile_area.pack(side="left", padx=4)
        self.sc_mobile_mid = ctk.CTkEntry(pf, width=60); self.sc_mobile_mid.pack(side="left", padx=2)
        ctk.CTkLabel(pf, text="-").pack(side="left")
        self.sc_mobile_end = ctk.CTkEntry(pf, width=60); self.sc_mobile_end.pack(side="left", padx=2)

    def _build_mamentor_tab(self, parent):
        self.mm_enabled = ctk.CTkCheckBox(parent, text="마멘토 활성화"); self.mm_enabled.select()
        self.mm_enabled.grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 4), sticky="w")

        ctk.CTkLabel(parent, text="아이디").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        self.mm_id = ctk.CTkEntry(parent, width=200); self.mm_id.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(parent, text="비밀번호").grid(row=1, column=2, padx=10, sticky="e")
        self.mm_pw = ctk.CTkEntry(parent, width=200, show="*"); self.mm_pw.grid(row=1, column=3, sticky="w")
        self.mm_login_btn = ctk.CTkButton(parent, text="로그인", width=80, command=self._mm_login); self.mm_login_btn.grid(row=1, column=4, padx=10)
        self.mm_status = ctk.CTkLabel(parent, text="미접속", text_color="#888"); self.mm_status.grid(row=1, column=5, sticky="w")

        ctk.CTkLabel(parent, text="자유홍보광고 게시판").grid(row=2, column=0, padx=10, pady=4, sticky="e")
        board_options = [f"{k} ({v})" for k, v in mamentor.FREE_AD_BOARDS.items()]
        self.mm_board = ctk.CTkOptionMenu(parent, values=board_options, width=260)
        self.mm_board.grid(row=2, column=1, columnspan=2, sticky="w")

        ctk.CTkLabel(parent, text="카테고리(ca_name)").grid(row=3, column=0, padx=10, pady=4, sticky="e")
        self.mm_caname = ctk.CTkEntry(parent, width=200, placeholder_text="비우면 게시판명 사용")
        self.mm_caname.grid(row=3, column=1, sticky="w")

    def _build_iboss_tab(self, parent):
        self.ib_enabled = ctk.CTkCheckBox(parent, text="아이보스 활성화 (일일 2회 자동제한)"); self.ib_enabled.select()
        self.ib_enabled.grid(row=0, column=0, columnspan=4, padx=10, pady=(10, 4), sticky="w")

        ctk.CTkLabel(parent, text="아이디(이메일)").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        self.ib_id = ctk.CTkEntry(parent, width=220); self.ib_id.grid(row=1, column=1, sticky="w")
        ctk.CTkLabel(parent, text="비밀번호").grid(row=1, column=2, padx=10, sticky="e")
        self.ib_pw = ctk.CTkEntry(parent, width=180, show="*"); self.ib_pw.grid(row=1, column=3, sticky="w")
        self.ib_login_btn = ctk.CTkButton(parent, text="로그인", width=80, command=self._ib_login); self.ib_login_btn.grid(row=1, column=4, padx=10)
        self.ib_status = ctk.CTkLabel(parent, text="미접속", text_color="#888"); self.ib_status.grid(row=1, column=5, sticky="w")

        ctk.CTkLabel(parent, text="구분 (매체/유형)").grid(row=2, column=0, padx=10, pady=4, sticky="e")
        cat_values = [f"{k} - {v}" for k, v in iboss.CATEGORY_OPTIONS.items()]
        self.ib_category = ctk.CTkOptionMenu(parent, values=cat_values, width=220); self.ib_category.grid(row=2, column=1, sticky="w")

        ctk.CTkLabel(parent, text="회사명").grid(row=2, column=2, padx=10, sticky="e")
        self.ib_company = ctk.CTkEntry(parent, width=180); self.ib_company.grid(row=2, column=3, sticky="w")

        ctk.CTkLabel(parent, text="담당자명").grid(row=3, column=0, padx=10, pady=4, sticky="e")
        self.ib_contact = ctk.CTkEntry(parent, width=180); self.ib_contact.grid(row=3, column=1, sticky="w")
        ctk.CTkLabel(parent, text="연락처").grid(row=3, column=2, padx=10, sticky="e")
        self.ib_phone = ctk.CTkEntry(parent, width=180); self.ib_phone.grid(row=3, column=3, sticky="w")

        ctk.CTkLabel(parent, text="이메일").grid(row=4, column=0, padx=10, pady=4, sticky="e")
        self.ib_email = ctk.CTkEntry(parent, width=180); self.ib_email.grid(row=4, column=1, sticky="w")
        ctk.CTkLabel(parent, text="카카오톡").grid(row=4, column=2, padx=10, sticky="e")
        self.ib_kakao = ctk.CTkEntry(parent, width=180); self.ib_kakao.grid(row=4, column=3, sticky="w")

        note = ctk.CTkLabel(parent, text="※ 이미지는 base64로 본문에 임베드 (브라우저 복사-붙여넣기와 동일 방식). 1MB 초과 이미지는 자동 제외.",
                            text_color="#81c784", font=("Pretendard", 10))
        note.grid(row=5, column=0, columnspan=6, padx=10, pady=10, sticky="w")

    # ============== 영업 크롤러 탭 ==============
    def _build_crawler_tab(self, parent):
        # 상단: 키워드/페이지/사이트 선택
        top = ctk.CTkFrame(parent)
        top.pack(fill="x", padx=10, pady=8)

        ctk.CTkLabel(top, text="키워드(쉼표)").grid(row=0, column=0, padx=6, pady=4, sticky="e")
        self.cr_keywords = ctk.CTkEntry(top, width=320, placeholder_text="예: 블로그, 인스타, 마케팅")
        self.cr_keywords.grid(row=0, column=1, padx=4, sticky="w")
        ctk.CTkLabel(top, text="페이지수").grid(row=0, column=2, padx=6, sticky="e")
        self.cr_pages = ctk.CTkEntry(top, width=60); self.cr_pages.insert(0, "2"); self.cr_pages.grid(row=0, column=3)
        ctk.CTkLabel(top, text="매칭").grid(row=0, column=4, padx=6, sticky="e")
        self.cr_match_in = ctk.CTkOptionMenu(top, values=["title_or_body", "title"], width=130)
        self.cr_match_in.grid(row=0, column=5, padx=4)
        ctk.CTkLabel(top, text="OP").grid(row=0, column=6, padx=4, sticky="e")
        self.cr_op = ctk.CTkOptionMenu(top, values=["or", "and"], width=70); self.cr_op.grid(row=0, column=7)

        # 사이트 체크박스 + 게시판
        site = ctk.CTkFrame(parent)
        site.pack(fill="x", padx=10, pady=4)

        self.cr_sc_enabled = ctk.CTkCheckBox(site, text="셀클럽 (maket_5_3 대행합니다)"); self.cr_sc_enabled.select()
        self.cr_sc_enabled.grid(row=0, column=0, padx=8, pady=4, sticky="w")
        sc_options = ["(전체+카테고리별)", "(전체)"] + SELLCLUB_CRAWL_CATEGORIES
        self.cr_sc_scope = ctk.CTkOptionMenu(site, values=sc_options, width=220)
        self.cr_sc_scope.grid(row=0, column=1, padx=4)

        self.cr_mm_enabled = ctk.CTkCheckBox(site, text="마멘토 게시판:"); self.cr_mm_enabled.select()
        self.cr_mm_enabled.grid(row=1, column=0, padx=8, pady=4, sticky="w")
        mm_options = ["(마멘토 탭 선택값)", "(전체)"] + [f"{k} ({v})" for k, v in mamentor.FREE_AD_BOARDS.items()]
        self.cr_mm_board = ctk.CTkOptionMenu(site, values=mm_options, width=260)
        self.cr_mm_board.set("(마멘토 탭 선택값)")
        self.cr_mm_board.grid(row=1, column=1, padx=4)

        self.cr_ib_enabled = ctk.CTkCheckBox(site, text="아이보스 (BD2986 바이럴서비스, 카테고리)"); self.cr_ib_enabled.select()
        self.cr_ib_enabled.grid(row=2, column=0, padx=8, pady=4, sticky="w")
        ib_options = ["(전체+카테고리별)", "(전체)"] + [f"{k} - {v}" for k, v in iboss.CATEGORY_OPTIONS.items()]
        self.cr_ib_cat = ctk.CTkOptionMenu(site, values=ib_options, width=180); self.cr_ib_cat.grid(row=2, column=1, padx=4)

        # 컨트롤 버튼
        ctrl = ctk.CTkFrame(parent)
        ctrl.pack(fill="x", padx=10, pady=6)
        self.cr_start_btn = ctk.CTkButton(ctrl, text="크롤링 시작", width=110, command=self._cr_start)
        self.cr_start_btn.grid(row=0, column=0, padx=4)
        self.cr_stop_btn = ctk.CTkButton(ctrl, text="중지", width=70, fg_color="#9c2c2c", state="disabled", command=self._cr_stop)
        self.cr_stop_btn.grid(row=0, column=1, padx=4)
        ctk.CTkButton(ctrl, text="목록 새로고침", width=110, command=self._cr_refresh).grid(row=0, column=2, padx=8)
        ctk.CTkButton(ctrl, text="엑셀 내보내기", width=110, command=self._cr_export).grid(row=0, column=3, padx=4)

        ctk.CTkLabel(ctrl, text="필터:").grid(row=0, column=4, padx=8)
        self.cr_filter_site = ctk.CTkOptionMenu(ctrl, values=["전체", "sellclub", "mamentor", "iboss"], width=110,
                                                 command=lambda _: self._cr_refresh())
        self.cr_filter_site.grid(row=0, column=5)
        self.cr_filter_status = ctk.CTkOptionMenu(ctrl, values=["전체"] + STATUS_OPTIONS, width=110,
                                                   command=lambda _: self._cr_refresh())
        self.cr_filter_status.grid(row=0, column=6, padx=4)
        self.cr_stats_label = ctk.CTkLabel(ctrl, text="총 0건")
        self.cr_stats_label.grid(row=0, column=7, padx=10)

        # 리드 목록 (TreeView from tk)
        from tkinter import ttk
        table_frame = ctk.CTkFrame(parent)
        table_frame.pack(fill="both", expand=True, padx=10, pady=6)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background="#2b2b2b", foreground="#fff", fieldbackground="#2b2b2b", rowheight=24)
        style.map("Treeview", background=[("selected", "#1f6aa5")])
        cols = ("id", "site", "status", "title", "company", "kakao", "phone", "email", "found")
        self.cr_tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        widths = {"id": 50, "site": 70, "status": 75, "title": 320, "company": 140,
                  "kakao": 160, "phone": 110, "email": 180, "found": 130}
        headers = {"id": "ID", "site": "사이트", "status": "상태", "title": "제목", "company": "회사",
                   "kakao": "카톡/오픈챗", "phone": "전화", "email": "이메일", "found": "수집"}
        for c in cols:
            self.cr_tree.heading(c, text=headers[c])
            self.cr_tree.column(c, width=widths[c], anchor="w")
        vs = ttk.Scrollbar(table_frame, orient="vertical", command=self.cr_tree.yview)
        self.cr_tree.configure(yscrollcommand=vs.set)
        self.cr_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.cr_tree.bind("<Double-1>", self._cr_open_detail)

    def _build_common_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(parent, text="콘텐츠 (messages/ 의 .txt + images/ 의 이미지)",
                     font=("Pretendard", 13, "bold")).grid(row=0, column=0, columnspan=5, sticky="w", padx=10, pady=(10, 4))

        self.msg_count = ctk.CTkLabel(parent, text="메시지: 0건"); self.msg_count.grid(row=1, column=0, padx=10, pady=4, sticky="w")
        self.img_count = ctk.CTkLabel(parent, text="이미지: 0건"); self.img_count.grid(row=1, column=1, padx=10, sticky="w")
        ctk.CTkLabel(parent, text="로테이션").grid(row=1, column=2, padx=10, sticky="e")
        self.rotation_mode = ctk.CTkOptionMenu(parent, values=["sequential", "random"]); self.rotation_mode.grid(row=1, column=3)
        ctk.CTkLabel(parent, text="이미지 첨부수").grid(row=1, column=4, padx=10, sticky="e")
        self.img_attach = ctk.CTkEntry(parent, width=60); self.img_attach.insert(0, "1"); self.img_attach.grid(row=1, column=5)
        ctk.CTkButton(parent, text="폴더 새로고침", width=120, command=self._refresh_resources).grid(row=1, column=6, padx=10)

        editor = ctk.CTkFrame(parent)
        editor.grid(row=2, column=0, columnspan=7, padx=10, pady=(8, 4), sticky="ew")
        editor.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(editor, text="제목").grid(row=0, column=0, padx=8, pady=(8, 4), sticky="e")
        self.msg_title_entry = ctk.CTkEntry(editor, placeholder_text="게시글 제목")
        self.msg_title_entry.grid(row=0, column=1, columnspan=5, padx=(0, 8), pady=(8, 4), sticky="ew")
        ctk.CTkLabel(editor, text="본문").grid(row=1, column=0, padx=8, pady=4, sticky="ne")
        self.msg_body_box = ctk.CTkTextbox(editor, height=120)
        self.msg_body_box.grid(row=1, column=1, columnspan=5, padx=(0, 8), pady=4, sticky="ew")
        ctk.CTkButton(editor, text="메시지 저장", width=110, command=self._save_message_from_editor).grid(row=2, column=1, padx=4, pady=(4, 8), sticky="w")
        ctk.CTkButton(editor, text="입력 지우기", width=100, command=self._clear_message_editor).grid(row=2, column=2, padx=4, pady=(4, 8), sticky="w")
        ctk.CTkButton(editor, text="이미지 추가", width=100, command=self._add_images_from_dialog).grid(row=2, column=3, padx=4, pady=(4, 8), sticky="w")
        ctk.CTkButton(editor, text="메시지 폴더", width=100, command=self._open_messages_dir).grid(row=2, column=4, padx=4, pady=(4, 8), sticky="w")
        ctk.CTkButton(editor, text="이미지 폴더", width=100, command=self._open_images_dir).grid(row=2, column=5, padx=4, pady=(4, 8), sticky="w")

        ctk.CTkLabel(parent, text="\n발송 방식: 한 라운드에 활성화된 모든 사이트에 같은 글+이미지 동시 게시\n간격은 모든 사이트 공통, 아이보스만 일일 2회 도달 시 자동 건너뜀",
                     text_color="#aaa", font=("Pretendard", 11)).grid(row=3, column=0, columnspan=7, padx=10, pady=6, sticky="w")

        sched = ctk.CTkFrame(parent)
        sched.grid(row=4, column=0, columnspan=7, padx=10, pady=(4, 4), sticky="ew")
        ctk.CTkLabel(sched, text="자동 스케줄", font=("Pretendard", 12, "bold")).grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.auto_post_enabled = ctk.CTkCheckBox(sched, text="발송")
        self.auto_post_enabled.grid(row=0, column=1, padx=6)
        self.auto_post_min = ctk.CTkEntry(sched, width=70)
        self.auto_post_min.insert(0, "120")
        self.auto_post_min.grid(row=0, column=2, padx=4)
        ctk.CTkLabel(sched, text="분마다").grid(row=0, column=3, padx=4)
        self.auto_crawl_enabled = ctk.CTkCheckBox(sched, text="크롤링")
        self.auto_crawl_enabled.grid(row=0, column=4, padx=12)
        self.auto_crawl_min = ctk.CTkEntry(sched, width=70)
        self.auto_crawl_min.insert(0, "60")
        self.auto_crawl_min.grid(row=0, column=5, padx=4)
        ctk.CTkLabel(sched, text="분마다").grid(row=0, column=6, padx=4)
        self.auto_start_btn = ctk.CTkButton(sched, text="스케줄 시작", width=110, command=self._auto_start)
        self.auto_start_btn.grid(row=0, column=7, padx=8)
        self.auto_stop_btn = ctk.CTkButton(sched, text="스케줄 중지", width=110, fg_color="#9c2c2c", state="disabled", command=self._auto_stop)
        self.auto_stop_btn.grid(row=0, column=8, padx=4)
        self.auto_status = ctk.CTkLabel(sched, text="중지됨", text_color="#888")
        self.auto_status.grid(row=0, column=9, padx=8, sticky="w")
        ctk.CTkLabel(sched, text="자동 발송 횟수").grid(row=1, column=0, padx=8, pady=(0, 8), sticky="w")
        ctk.CTkLabel(sched, text="셀클럽").grid(row=1, column=1, padx=(6, 2), pady=(0, 8), sticky="e")
        self.auto_sc_count = ctk.CTkEntry(sched, width=54)
        self.auto_sc_count.insert(0, "10")
        self.auto_sc_count.grid(row=1, column=2, padx=(0, 8), pady=(0, 8))
        ctk.CTkLabel(sched, text="마멘토").grid(row=1, column=3, padx=(6, 2), pady=(0, 8), sticky="e")
        self.auto_mm_count = ctk.CTkEntry(sched, width=54)
        self.auto_mm_count.insert(0, "10")
        self.auto_mm_count.grid(row=1, column=4, padx=(0, 8), pady=(0, 8))
        ctk.CTkLabel(sched, text="아이보스").grid(row=1, column=5, padx=(6, 2), pady=(0, 8), sticky="e")
        self.auto_ib_count = ctk.CTkEntry(sched, width=54)
        self.auto_ib_count.insert(0, "2")
        self.auto_ib_count.grid(row=1, column=6, padx=(0, 8), pady=(0, 8))
        ctk.CTkLabel(sched, text="0이면 제외, 아이보스는 일일 2회 제한 적용", text_color="#aaa", font=("Pretendard", 10)).grid(row=1, column=7, columnspan=3, padx=8, pady=(0, 8), sticky="w")

    # ---------- 저장/로드 ----------
    def _load_saved(self):
        s = load_settings()
        self.sc_id.insert(0, s.get("sc_id", ""))
        self.mm_id.insert(0, s.get("mm_id", ""))
        self.ib_id.insert(0, s.get("ib_id", ""))
        if s.get("sc_phone_mid"):
            self.sc_phone_mid.insert(0, s["sc_phone_mid"])
        if s.get("sc_phone_end"):
            self.sc_phone_end.insert(0, s["sc_phone_end"])
        if s.get("sc_mobile_mid"):
            self.sc_mobile_mid.insert(0, s["sc_mobile_mid"])
        if s.get("sc_mobile_end"):
            self.sc_mobile_end.insert(0, s["sc_mobile_end"])
        if s.get("ib_company"):
            self.ib_company.insert(0, s["ib_company"])
        if s.get("ib_contact"):
            self.ib_contact.insert(0, s["ib_contact"])
        if s.get("ib_phone"):
            self.ib_phone.insert(0, s["ib_phone"])
        if s.get("ib_email"):
            self.ib_email.insert(0, s["ib_email"])
        if s.get("ib_kakao"):
            self.ib_kakao.insert(0, s["ib_kakao"])

    def _save_field(self, key, val):
        s = load_settings(); s[key] = val; save_settings(s)

    def _log(self, msg):
        from datetime import datetime
        self.log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see("end")

    def _refresh_resources(self):
        os.makedirs(resource_path(MESSAGES_DIR), exist_ok=True)
        os.makedirs(resource_path(IMAGES_DIR), exist_ok=True)
        m = load_messages(); i = list_images()
        self.msg_count.configure(text=f"메시지: {len(m)}건")
        self.img_count.configure(text=f"이미지: {len(i)}건")

    def _save_message_from_editor(self):
        title = self.msg_title_entry.get().strip()
        body = self.msg_body_box.get("1.0", "end").strip()
        if not title:
            messagebox.showwarning("메시지 저장", "제목을 입력하세요.")
            return
        if not body:
            messagebox.showwarning("메시지 저장", "본문을 입력하세요.")
            return

        msg_dir = resource_path(MESSAGES_DIR)
        os.makedirs(msg_dir, exist_ok=True)
        safe_title = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", title).strip("_")[:40] or "message"
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_title}.txt"
        path = os.path.join(msg_dir, fname)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"{title}\n{body}\n")
        self._refresh_resources()
        self._log(f"[메시지] 저장됨: {fname}")
        messagebox.showinfo("메시지 저장", f"저장됐습니다.\n{fname}")

    def _clear_message_editor(self):
        self.msg_title_entry.delete(0, "end")
        self.msg_body_box.delete("1.0", "end")

    def _add_images_from_dialog(self):
        paths = filedialog.askopenfilenames(
            title="이미지 선택",
            filetypes=[
                ("Image files", "*.jpg *.jpeg *.png *.gif *.webp *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return
        img_dir = resource_path(IMAGES_DIR)
        os.makedirs(img_dir, exist_ok=True)
        copied = 0
        for src in paths:
            name = os.path.basename(src)
            stem, ext = os.path.splitext(name)
            dst = os.path.join(img_dir, name)
            if os.path.exists(dst):
                dst = os.path.join(img_dir, f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}")
            shutil.copy2(src, dst)
            copied += 1
        self._refresh_resources()
        self._log(f"[이미지] {copied}개 추가")
        messagebox.showinfo("이미지 추가", f"이미지 {copied}개를 추가했습니다.")

    def _open_messages_dir(self):
        path = resource_path(MESSAGES_DIR)
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def _open_images_dir(self):
        path = resource_path(IMAGES_DIR)
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    def _check_update_async(self):
        def worker():
            try:
                info = updater.check_for_update()
            except Exception:
                return
            if info:
                self.after(0, lambda: self._prompt_update(info))

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_update(self, info: updater.UpdateInfo):
        notes = f"\n\n{info.notes}" if info.notes else ""
        ok = messagebox.askyesno(
            "업데이트",
            f"새 버전이 있습니다.\n현재 버전: {APP_VERSION}\n최신 버전: {info.version}{notes}\n\n지금 업데이트할까요?",
        )
        if not ok:
            return
        try:
            self._log(f"[업데이트] {info.version} 다운로드 중...")
            downloaded = updater.download_update(info)
            if getattr(sys, "frozen", False):
                self._log("[업데이트] 프로그램을 재시작합니다.")
                updater.install_and_restart(downloaded)
            else:
                messagebox.showinfo("업데이트", f"개발 실행 상태라 자동 교체는 생략했습니다.\n다운로드: {downloaded}")
        except Exception as e:
            messagebox.showerror("업데이트 실패", str(e))

    def _auto_start(self):
        if self.auto_scheduler_thread and self.auto_scheduler_thread.is_alive():
            return
        post_enabled = bool(self.auto_post_enabled.get())
        crawl_enabled = bool(self.auto_crawl_enabled.get())
        if not post_enabled and not crawl_enabled:
            messagebox.showwarning("자동 스케줄", "발송 또는 크롤링 중 하나 이상 선택하세요.")
            return
        post_counts: dict[str, int] = {}
        if post_enabled:
            try:
                post_counts = {
                    "sellclub": max(0, int(self.auto_sc_count.get() or 0)),
                    "mamentor": max(0, int(self.auto_mm_count.get() or 0)),
                    "iboss": max(0, int(self.auto_ib_count.get() or 0)),
                }
            except ValueError:
                messagebox.showerror("자동 스케줄", "사이트별 발송 횟수는 숫자로 입력하세요.")
                return
            if not any(post_counts.values()):
                messagebox.showwarning("자동 스케줄", "사이트별 발송 횟수를 하나 이상 1 이상으로 입력하세요.")
                return
        try:
            post_sec = max(60, int(float(self.auto_post_min.get()) * 60))
            crawl_sec = max(60, int(float(self.auto_crawl_min.get()) * 60))
        except ValueError:
            messagebox.showerror("자동 스케줄", "간격은 숫자(분)로 입력하세요.")
            return

        self.auto_scheduler_stop.clear()
        self.auto_scheduler_thread = threading.Thread(
            target=self._auto_loop,
            args=(post_enabled, crawl_enabled, post_sec, crawl_sec, post_counts),
            daemon=True,
        )
        self.auto_scheduler_thread.start()
        self.auto_start_btn.configure(state="disabled")
        self.auto_stop_btn.configure(state="normal")
        self.auto_status.configure(text="실행 중", text_color="#81c784")
        target_label = ", ".join(f"{k} {v}회" for k, v in post_counts.items() if v > 0) if post_counts else "-"
        self._log(f"[자동 스케줄] 시작: 발송={post_enabled}({post_sec//60}분마다/{target_label}), 크롤링={crawl_enabled}({crawl_sec//60}분마다)")

    def _auto_stop(self):
        self.auto_scheduler_stop.set()
        self.auto_status.configure(text="중지 요청", text_color="#ffcc80")
        self._log("[자동 스케줄] 중지 요청")

    def _auto_loop(self, post_enabled: bool, crawl_enabled: bool, post_sec: int, crawl_sec: int, post_counts: dict[str, int]):
        next_post = time.monotonic()
        next_crawl = time.monotonic()
        remaining = dict(post_counts)
        post_done_logged = False
        try:
            while not self.auto_scheduler_stop.is_set():
                now = time.monotonic()
                if post_enabled and now >= next_post:
                    post_sites = tuple(name for name, count in remaining.items() if count > 0)
                    if post_sites:
                        self.after(0, self._auto_try_start_post, post_sites, remaining)
                    elif not post_done_logged:
                        self.after(0, self._log, "[자동 스케줄] 사이트별 발송 횟수 완료")
                        post_done_logged = True
                        post_enabled = False
                        if not crawl_enabled:
                            self.auto_scheduler_stop.set()
                    next_post = now + post_sec
                if crawl_enabled and now >= next_crawl:
                    self.after(0, self._auto_try_start_crawl)
                    next_crawl = now + crawl_sec
                time.sleep(1)
        finally:
            self.after(0, self._auto_stopped)

    def _auto_stopped(self):
        self.auto_start_btn.configure(state="normal")
        self.auto_stop_btn.configure(state="disabled")
        self.auto_status.configure(text="중지됨", text_color="#888")

    def _auto_try_start_post(self, post_sites: tuple[str, ...] = (), remaining: dict[str, int] | None = None):
        if self.job and self.job.is_running():
            self._log("[자동 스케줄] 발송 건너뜀: 이미 발송 중")
            return
        if self.crawl_job and self.crawl_job.is_running():
            self._log("[자동 스케줄] 발송 건너뜀: 크롤링 중")
            return
        self._log(f"[자동 스케줄] 발송 시작: {', '.join(post_sites) if post_sites else '활성 사이트'}")
        started = self._start_job(set(post_sites) if post_sites else None, repeat_count=1)
        if started and remaining is not None:
            for name in post_sites:
                remaining[name] = max(0, remaining.get(name, 0) - 1)
            rest = ", ".join(f"{k} {v}회" for k, v in remaining.items() if v > 0) or "없음"
            self._log(f"[자동 스케줄] 남은 발송 횟수: {rest}")

    def _auto_try_start_crawl(self):
        if self.crawl_job and self.crawl_job.is_running():
            self._log("[자동 스케줄] 크롤링 건너뜀: 이미 크롤링 중")
            return
        if self.job and self.job.is_running():
            self._log("[자동 스케줄] 크롤링 건너뜀: 발송 중")
            return
        self._log("[자동 스케줄] 크롤링 시작")
        self._cr_start()

    # ---------- 사이트별 로그인 ----------
    def _sc_login(self):
        uid, pw = self.sc_id.get().strip(), self.sc_pw.get().strip()
        if not uid or not pw:
            messagebox.showwarning("입력", "셀클럽 ID/PW 입력"); return
        self._sc_login_async(uid, pw)

    def _sc_login_async(self, uid, pw):
        self.sc_login_btn.configure(state="disabled"); self.sc_status.configure(text="로그인 중...", text_color="#888"); self.update()
        try:
            c = sellclub.SellClubClient()
            c.login(uid, pw)
            self.sellclub_client = c
            self.sc_status.configure(text=f"OK ({uid})", text_color="#81c784")
            self.sc_login_btn.configure(state="normal", text="재로그인")
            self._save_field("sc_id", uid)
            self._log(f"[셀클럽] 로그인 성공: {uid}")
        except sellclub.SellClubError as e:
            self.sc_status.configure(text="실패", text_color="#e57373"); messagebox.showerror("셀클럽 로그인 실패", str(e))
            self.sc_login_btn.configure(state="normal")

    def _mm_login(self):
        uid, pw = self.mm_id.get().strip(), self.mm_pw.get().strip()
        if not uid or not pw:
            messagebox.showwarning("입력", "마멘토 ID/PW 입력"); return
        self.mm_login_btn.configure(state="disabled"); self.mm_status.configure(text="로그인 중...", text_color="#888"); self.update()
        try:
            c = mamentor.MamentorClient()
            c.login(uid, pw)
            self.mamentor_client = c
            self.mm_status.configure(text=f"OK ({uid})", text_color="#81c784")
            self.mm_login_btn.configure(state="normal", text="재로그인")
            self._save_field("mm_id", uid)
            self._log(f"[마멘토] 로그인 성공: {uid}")
        except mamentor.MamentorError as e:
            self.mm_status.configure(text="실패", text_color="#e57373"); messagebox.showerror("마멘토 로그인 실패", str(e))
            self.mm_login_btn.configure(state="normal")

    def _ib_login(self):
        uid, pw = self.ib_id.get().strip(), self.ib_pw.get().strip()
        if not uid or not pw:
            messagebox.showwarning("입력", "아이보스 ID/PW 입력"); return
        self.ib_login_btn.configure(state="disabled"); self.ib_status.configure(text="로그인 중...", text_color="#888"); self.update()
        try:
            c = iboss.IBossClient()
            c.login(uid, pw)
            self.iboss_client = c
            self.ib_status.configure(text=f"OK ({uid})", text_color="#81c784")
            self.ib_login_btn.configure(state="normal", text="재로그인")
            self._save_field("ib_id", uid)
            self._log(f"[아이보스] 로그인 성공: {uid}")
        except iboss.IBossError as e:
            self.ib_status.configure(text="실패", text_color="#e57373"); messagebox.showerror("아이보스 로그인 실패", str(e))
            self.ib_login_btn.configure(state="normal")

    # ---------- 옵션 수집 ----------
    def _collect_sc_options(self):
        pt = self.sc_posttype.get().split(" ")[0]
        return sellclub.WriteOptions(
            category=self.sc_category.get(),
            deal_status=self.sc_dealstatus.get(),
            reg_class=self.sc_reg.get(),
            deal_method=self.sc_dealmethod.get().strip() or "쪽지연락",
            post_type=pt,
            phone_area=self.sc_phone_area.get(),
            phone_mid=self.sc_phone_mid.get().strip(),
            phone_end=self.sc_phone_end.get().strip(),
            mobile_area=self.sc_mobile_area.get(),
            mobile_mid=self.sc_mobile_mid.get().strip(),
            mobile_end=self.sc_mobile_end.get().strip(),
        )

    def _collect_mm_options(self):
        bo_table = self.mm_board.get().split(" ")[0]
        return mamentor.WriteOptions(
            bo_table=bo_table,
            ca_name=self.mm_caname.get().strip(),
        )

    def _collect_ib_options(self):
        cat = self.ib_category.get().split(" ")[0]
        return iboss.WriteOptions(
            category_1=cat,
            company_name=self.ib_company.get().strip(),
            contact_name=self.ib_contact.get().strip(),
            phone=self.ib_phone.get().strip(),
            email=self.ib_email.get().strip(),
            kakao=self.ib_kakao.get().strip(),
        )

    def _set_post_buttons_state(self, state: str):
        for btn in (self.start_btn, self.start_sc_only_btn, self.start_mm_only_btn, self.start_ib_only_btn):
            btn.configure(state=state)

    # ---------- 발송 시작 ----------
    def _start_job(self, target_sites: set[str] | None = None, repeat_count: int | None = None) -> bool:
        if self.job and self.job.is_running():
            messagebox.showwarning("발송 중", "이미 발송이 진행 중입니다.")
            return False

        plans: dict[str, SitePlan] = {}

        def selected(site: str, checkbox) -> bool:
            return site in target_sites if target_sites is not None else bool(checkbox.get())

        if selected("sellclub", self.sc_enabled):
            if not self.sellclub_client or not self.sellclub_client.logged_in:
                messagebox.showwarning("셀클럽", "셀클럽 로그인이 필요합니다 (탭에서 로그인)"); return False
            opts = self._collect_sc_options()
            if not (opts.mobile_mid and opts.mobile_end):
                messagebox.showwarning("셀클럽", "핸드폰 번호 모두 입력 (필수 항목)"); return False
            plans["sellclub"] = SitePlan(enabled=True, client=self.sellclub_client, options=opts, daily_limit=DEFAULT_DAILY_LIMITS["sellclub"], image_supported=True)

        if selected("mamentor", self.mm_enabled):
            if not self.mamentor_client or not self.mamentor_client.logged_in:
                messagebox.showwarning("마멘토", "마멘토 로그인이 필요합니다"); return False
            plans["mamentor"] = SitePlan(enabled=True, client=self.mamentor_client, options=self._collect_mm_options(), daily_limit=DEFAULT_DAILY_LIMITS["mamentor"], image_supported=True)

        if selected("iboss", self.ib_enabled):
            if not self.iboss_client or not self.iboss_client.logged_in:
                messagebox.showwarning("아이보스", "아이보스 로그인이 필요합니다"); return False
            ib_opts = self._collect_ib_options()
            if not ib_opts.company_name or not ib_opts.contact_name:
                messagebox.showwarning("아이보스", "회사명, 담당자명 필수"); return False
            if not ib_opts.has_any_contact():
                messagebox.showwarning("아이보스", "연락처/이메일/카카오톡 중 하나 이상 필수"); return False
            plans["iboss"] = SitePlan(enabled=True, client=self.iboss_client, options=ib_opts, daily_limit=DEFAULT_DAILY_LIMITS["iboss"], image_supported=True)

        if not plans:
            messagebox.showwarning("사이트 선택", "최소 1개 사이트를 선택해야 합니다"); return False

        # 콘텐츠
        posts = load_messages()
        if not posts:
            messagebox.showwarning("메시지 없음", f"{MESSAGES_DIR}/ 에 .txt 추가하세요 (첫 줄=제목)"); return False
        images = list_images()
        try:
            attach = int(self.img_attach.get())
        except ValueError:
            attach = 0
        try:
            rotator = Rotator(posts, images, mode=self.rotation_mode.get(), attach_image_count=attach)
        except ValueError as e:
            messagebox.showerror("오류", str(e)); return False

        try:
            cfg = JobConfig(
                repeat_count=max(1, int(repeat_count if repeat_count is not None else self.repeat_count.get())),
                interval_sec=max(5, int(self.interval.get())),
                jitter_sec=max(0, int(self.jitter.get())),
            )
        except ValueError:
            messagebox.showerror("입력", "반복횟수/간격은 숫자"); return False

        # 셀클럽/아이보스 필드 저장
        s = load_settings()
        if "sellclub" in plans:
            sco = plans["sellclub"].options
            s.update({"sc_phone_mid": sco.phone_mid, "sc_phone_end": sco.phone_end, "sc_mobile_mid": sco.mobile_mid, "sc_mobile_end": sco.mobile_end})
        if "iboss" in plans:
            ibo = plans["iboss"].options
            s.update({"ib_company": ibo.company_name, "ib_contact": ibo.contact_name, "ib_phone": ibo.phone, "ib_email": ibo.email, "ib_kakao": ibo.kakao})
        save_settings(s)

        bot = MultiBot(plans)
        self.job = PostingJob(bot, rotator, cfg, on_log=self._log)
        self.job.start()
        self._set_post_buttons_state("disabled"); self.stop_btn.configure(state="normal")
        self._log(f"━━━ 발송 시작: {cfg.repeat_count}라운드, {cfg.interval_sec}±{cfg.jitter_sec}초 ━━━")
        self._log(f"활성 사이트: {', '.join(plans.keys())}")
        self.after(1000, self._poll_job)
        return True

    def _stop_job(self):
        if self.job and self.job.is_running():
            self.job.stop(); self._log("중지 요청 전송...")

    def _poll_job(self):
        if self.job and not self.job.is_running():
            self._set_post_buttons_state("normal"); self.stop_btn.configure(state="disabled"); return
        if self.job:
            self.after(1000, self._poll_job)


    # ============== 영업 크롤러 동작 ==============
    def _cr_start(self):
        keywords_raw = self.cr_keywords.get().strip()
        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
        try:
            pages = max(1, int(self.cr_pages.get()))
        except ValueError:
            pages = 2

        crawlers = []
        cfg_map: dict[str, CrawlConfig] = {}

        common = dict(
            keywords=keywords,
            pages_per_board=pages,
            page_delay_min=3.0,
            page_delay_max=6.0,
            detail_delay_min=1.5,
            detail_delay_max=3.0,
            deep_stop_pages=2,
            fetch_detail=True,
            match_in=self.cr_match_in.get(),
            keyword_op=self.cr_op.get(),
        )

        if self.cr_sc_enabled.get():
            if not self.sellclub_client or not self.sellclub_client.logged_in:
                messagebox.showwarning("셀클럽", "셀클럽 탭에서 먼저 로그인하세요"); return
            sc_scope = self.cr_sc_scope.get()
            if sc_scope.startswith("(전체+"):
                sc_boards = ["maket_5_3"] + [f"maket_5_3::{cat}" for cat in SELLCLUB_CRAWL_CATEGORIES]
            elif sc_scope.startswith("(전체)"):
                sc_boards = ["maket_5_3"]
            else:
                sc_boards = [f"maket_5_3::{sc_scope}"]
            crawlers.append(SellClubCrawler(self.sellclub_client))
            cfg_map["sellclub"] = CrawlConfig(boards=sc_boards, **common)

        if self.cr_mm_enabled.get():
            if not self.mamentor_client or not self.mamentor_client.logged_in:
                messagebox.showwarning("마멘토", "마멘토 탭에서 먼저 로그인하세요"); return
            mm_choice = self.cr_mm_board.get()
            if mm_choice.startswith("(마멘토"):
                mm_boards = [self.mm_board.get().split(" ")[0]]
            elif mm_choice.startswith("(전체)"):
                mm_boards = list(mamentor.FREE_AD_BOARDS.keys())
            else:
                mm_boards = [mm_choice.split(" ")[0]]
            crawlers.append(MamentorCrawler(self.mamentor_client))
            cfg_map["mamentor"] = CrawlConfig(boards=mm_boards, **common)

        if self.cr_ib_enabled.get():
            if not self.iboss_client or not self.iboss_client.logged_in:
                messagebox.showwarning("아이보스", "아이보스 탭에서 먼저 로그인하세요"); return
            cat = self.cr_ib_cat.get()
            if cat.startswith("(전체+"):
                ib_boards = [""] + list(iboss.CATEGORY_OPTIONS.keys())
            elif cat.startswith("(전체)"):
                ib_boards = []
            else:
                ib_boards = [cat.split(" ")[0]]
            crawlers.append(IBossCrawler(self.iboss_client))
            cfg_map["iboss"] = CrawlConfig(boards=ib_boards, **common)

        if not crawlers:
            messagebox.showwarning("사이트", "최소 1개 사이트는 활성화해야 합니다"); return

        self._log(f"━━━ 크롤링 시작: 사이트 {len(crawlers)}개, 키워드 {keywords or '전체'}, {pages}페이지 ━━━")
        self.crawl_job = CrawlJob(
            crawlers=crawlers,
            cfg_per_site=cfg_map,
            on_log=self._log,
            on_lead=lambda L: self.after(0, self._cr_append_row, L),
            on_done=lambda counts: self.after(0, self._cr_done, counts),
        )
        self.crawl_job.start()
        self.cr_start_btn.configure(state="disabled"); self.cr_stop_btn.configure(state="normal")

    def _cr_stop(self):
        if self.crawl_job and self.crawl_job.is_running():
            self.crawl_job.stop()
            self._log("크롤링 중지 요청 전송...")

    def _cr_done(self, counts: dict):
        self.cr_start_btn.configure(state="normal"); self.cr_stop_btn.configure(state="disabled")
        self._cr_refresh()

    def _cr_append_row(self, L):
        L = get_lead(L.id) or L
        site = self.cr_filter_site.get()
        status = self.cr_filter_status.get()
        if site != "전체" and L.site != site:
            return
        if status != "전체" and L.status != status:
            return
        # 실시간 추가: 중복 방지 (id 기준)
        for iid in self.cr_tree.get_children():
            if self.cr_tree.set(iid, "id") == str(L.id):
                self.cr_tree.item(iid, values=(
                    L.id, L.site, L.status, L.title[:80], L.company,
                    ", ".join((L.kakao_ids or []) + (L.open_chats or []))[:60],
                    ", ".join(L.phones or []),
                    ", ".join(L.emails or [])[:50],
                    L.found_at[:16] if L.found_at else "",
                ))
                self.cr_tree.see(iid)
                return
        iid = self.cr_tree.insert("", "end", values=(
            L.id, L.site, L.status, L.title[:80], L.company,
            ", ".join((L.kakao_ids or []) + (L.open_chats or []))[:60],
            ", ".join(L.phones or []),
            ", ".join(L.emails or [])[:50],
            L.found_at[:16] if L.found_at else "",
        ))
        self.cr_tree.see(iid)

    def _cr_refresh(self):
        for iid in self.cr_tree.get_children():
            self.cr_tree.delete(iid)
        site = self.cr_filter_site.get()
        status = self.cr_filter_status.get()
        leads = get_leads(
            site=None if site == "전체" else site,
            status=None if status == "전체" else status,
            limit=1000,
        )
        for L in leads:
            self.cr_tree.insert("", "end", values=(
                L.id, L.site, L.status, L.title[:80], L.company,
                ", ".join((L.kakao_ids or []) + (L.open_chats or []))[:60],
                ", ".join(L.phones or []),
                ", ".join(L.emails or [])[:50],
                L.found_at[:16] if L.found_at else "",
            ))
        s = crawler_stats()
        self.cr_stats_label.configure(text=f"총 {s['total']}건 · {s['by_site']} · {s['by_status']}")

    def _cr_export(self):
        try:
            path = export_leads()
            messagebox.showinfo("내보내기", f"엑셀 저장됨:\n{path}")
            self._log(f"엑셀 내보내기: {path}")
        except Exception as e:
            messagebox.showerror("내보내기 실패", str(e))

    def _cr_open_detail(self, event):
        sel = self.cr_tree.selection()
        if not sel:
            return
        lead_id = int(self.cr_tree.set(sel[0], "id"))
        lead = get_lead(lead_id)
        if lead:
            LeadDetailDialog(self, lead, on_change=self._cr_refresh)


class LeadDetailDialog(ctk.CTkToplevel):
    """리드 상세 + 접촉이력 + 상태 변경."""

    def __init__(self, parent, lead, on_change=None):
        super().__init__(parent)
        self.lead = lead
        self.on_change = on_change
        self.title(f"[{lead.site}] {lead.title[:50]}")
        self.geometry("720x600")

        info = ctk.CTkScrollableFrame(self, height=260)
        info.pack(fill="x", padx=10, pady=10)

        def row(label, value):
            f = ctk.CTkFrame(info, fg_color="transparent"); f.pack(fill="x", pady=2)
            ctk.CTkLabel(f, text=label, width=100, anchor="e").pack(side="left", padx=6)
            ctk.CTkLabel(f, text=str(value), anchor="w", wraplength=560, justify="left").pack(side="left", padx=4)

        row("사이트", lead.site)
        row("게시판", lead.board)
        row("카테고리", lead.category)
        row("제목", lead.title)
        row("회사", lead.company)
        row("카톡 ID", ", ".join(lead.kakao_ids))
        row("오픈채팅", ", ".join(lead.open_chats))
        row("전화", ", ".join(lead.phones))
        row("이메일", ", ".join(lead.emails))
        row("작성자", lead.writer)
        row("게시일", lead.posted_at)
        row("매칭키워드", ", ".join(lead.matched_keywords))
        row("URL", lead.post_url)
        row("본문", lead.body_text or lead.body_excerpt or "(상세 미수집)")

        # 상태 변경
        status_frame = ctk.CTkFrame(self); status_frame.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(status_frame, text="상태").pack(side="left", padx=6)
        self.status_var = ctk.CTkOptionMenu(status_frame, values=STATUS_OPTIONS); self.status_var.set(lead.status)
        self.status_var.pack(side="left", padx=4)
        ctk.CTkButton(status_frame, text="상태 저장", width=100, command=self._save_status).pack(side="left", padx=4)

        # 접촉 이력 입력
        contact_frame = ctk.CTkFrame(self); contact_frame.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(contact_frame, text="채널").grid(row=0, column=0, padx=4)
        self.ct_channel = ctk.CTkOptionMenu(contact_frame, values=CHANNEL_OPTIONS, width=110); self.ct_channel.grid(row=0, column=1)
        ctk.CTkLabel(contact_frame, text="결과").grid(row=0, column=2, padx=4)
        self.ct_result = ctk.CTkOptionMenu(contact_frame, values=RESULT_OPTIONS, width=110); self.ct_result.grid(row=0, column=3)
        ctk.CTkLabel(contact_frame, text="메모").grid(row=0, column=4, padx=4)
        self.ct_note = ctk.CTkEntry(contact_frame, width=260); self.ct_note.grid(row=0, column=5)
        ctk.CTkButton(contact_frame, text="접촉 기록 추가", width=140, command=self._add_contact).grid(row=0, column=6, padx=8)

        # 접촉 이력 목록
        hist = ctk.CTkLabel(self, text="접촉 이력", font=("Pretendard", 13, "bold"))
        hist.pack(anchor="w", padx=10)
        self.hist_box = ctk.CTkTextbox(self, height=180)
        self.hist_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._refresh_history()

    def _refresh_history(self):
        self.hist_box.delete("0.0", "end")
        for c in get_contacts(self.lead.id):
            self.hist_box.insert("end", f"[{c.attempted_at}] {c.channel} → {c.result}  {c.note or ''}\n")

    def _save_status(self):
        update_status(self.lead.id, self.status_var.get())
        if self.on_change:
            self.on_change()
        messagebox.showinfo("저장됨", "상태가 업데이트되었습니다.")

    def _add_contact(self):
        c = Contact(
            lead_id=self.lead.id,
            attempted_at=datetime.now().isoformat(timespec="seconds"),
            channel=self.ct_channel.get(),
            result=self.ct_result.get(),
            note=self.ct_note.get().strip(),
        )
        add_contact(c)
        self.ct_note.delete(0, "end")
        self._refresh_history()
        if self.on_change:
            self.on_change()


def main():
    login = LoginWindow(); login.mainloop()
    if not login.authenticated:
        sys.exit(0)
    MainApp().mainloop()


if __name__ == "__main__":
    main()
