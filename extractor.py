"""게시글 본문/메타에서 카톡/전화/이메일/회사명 추출.

여러 표기 패턴 (카톡, kt, 카카오, talkID, 오픈채팅 링크 등) 을 처리.
한국 게시판 광고글 텍스트 기준으로 튜닝됨.
"""

from __future__ import annotations

import base64
import re
import html as html_lib
from dataclasses import dataclass, field


@dataclass
class ContactInfo:
    kakao_ids: list[str] = field(default_factory=list)
    open_chat_urls: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    company: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.kakao_ids or self.open_chat_urls or self.phones or self.emails)

    @property
    def primary_kakao(self) -> str:
        if self.kakao_ids:
            return self.kakao_ids[0]
        if self.open_chat_urls:
            return self.open_chat_urls[0]
        return ""

    @property
    def primary_phone(self) -> str:
        return self.phones[0] if self.phones else ""

    @property
    def primary_email(self) -> str:
        return self.emails[0] if self.emails else ""


@dataclass
class PriceHit:
    amount: int
    raw: str
    context: str
    start: int


# ────────── 정규식 패턴들 ──────────

# 전화/핸드폰: 010-1234-5678, 02-123-4567, 010 1234 5678, 01012345678
PHONE_RE = re.compile(r"""
    (?<![0-9])
    (?:
        (?:0(?:1[016789]|2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70|505))   # area
        [\-\s.)]?
        \d{3,4}
        [\-\s.]?
        \d{4}
    )
    (?![0-9])
""", re.VERBOSE)

# 이메일
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 카카오 오픈채팅 링크: https://open.kakao.com/o/XXXXX
OPENCHAT_RE = re.compile(r"https?://open\.kakao\.com/o/[A-Za-z0-9]+")

# 단가: 100원, 1,000원, 0.5만원, 1천원 등
PRICE_RE = re.compile(
    r"(?<![0-9])(?P<number>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?P<unit>만\s*원|만원|천\s*원|천원|원)"
)

# 카톡 ID 표기들. 키워드 + 구분자 + ID
# 예: 카톡:abc123, 카카오톡 abc123, kt: abc, 카톡ID abc123, 오픈톡 abc123
KAKAO_KW_RE = re.compile(
    r"""
    (?:카\s?톡\s?(?:ID|아이디|id)?|카카오\s?톡\s?(?:ID|id|아이디)?|kakao\s?(?:talk|tok)?|kakaotalk|
       kt|오픈톡|오픈\s?채팅|talk\s?id|talkID|톡\s?id|톡\s?아이디)
    \s*[:：=\-]?\s*
    ([A-Za-z][A-Za-z0-9_.\-]{2,30})
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 회사/상호명: "회사명: XXX" "상호: XXX" "업체명: XXX"
# 라벨과 값 사이에 ":" 또는 ":" 같은 명확한 구분자가 있어야 함.
# 짧은 회사명(40자 이내) + 안전한 문자만 (개행/공백 줄이고 광고 본문 long-text 거름)
COMPANY_RE = re.compile(
    r"""(?:^|[\n\r\s]|[│|・▶▷*\-=]+)
        (?:회사명|상호명|상호|업체명|업체|회사)
        \s*[:：]\s*
        ([^\n\r<>│|]{2,30}?)
        (?=[\n\r<]|[│|・▶▷]|\s{2,}|$)
    """,
    re.VERBOSE,
)

# HTML 태그 제거용
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def html_to_text(html: str) -> str:
    """HTML 본문을 plain text 로 변환 (정규식 추출용)."""
    if not html:
        return ""
    text = BR_RE.sub("\n", html)
    text = TAG_RE.sub(" ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def normalize_phone(raw: str) -> str:
    """전화번호 정규화: 010-1234-5678 형식으로."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("01"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10:
        if digits.startswith("02"):
            return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    if len(digits) == 9 and digits.startswith("02"):
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    return raw.strip()


# 알려진 false-positive (운영진 블랙리스트 등)
KAKAO_BLOCKLIST = {
    "naver", "gmail", "nate", "daum", "hanmail", "yahoo", "kakao",
    "open", "talk", "test", "example", "kakaotalk", "facebook",
}

PRICE_KEYWORD_TERMS = [
    "구글", "google", "네이버", "naver", "카카오", "kakao", "쿠팡", "coupang",
    "배민", "인스타", "instagram", "유튜브", "youtube", "플레이스", "지도", "맵",
    "리뷰", "review", "영수증", "방문자", "블로그", "카페", "자동완성", "검색노출",
]
PRICE_NOISE_TERMS = {"최저가", "최저", "단가", "가격", "저렴", "대행", "진행"}
GENERIC_PRICE_TERMS = {"리뷰", "review"}


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _price_amount(number: str, unit: str) -> int:
    value = float(number.replace(",", ""))
    unit = unit.replace(" ", "")
    if unit.startswith("만"):
        value *= 10_000
    elif unit.startswith("천"):
        value *= 1_000
    return int(round(value))


def _price_context(text: str, start: int, end: int, radius: int = 80) -> str:
    line_start = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
    next_lf = text.find("\n", end)
    next_cr = text.find("\r", end)
    line_end_candidates = [pos for pos in (next_lf, next_cr) if pos != -1]
    line_end = min(line_end_candidates) if line_end_candidates else len(text)
    line = re.sub(r"\s+", " ", text[line_start:line_end]).strip()
    if line and len(line) <= 160:
        compact_line = _compact(line)
        has_service_term = any(term in compact_line for term in PRICE_KEYWORD_TERMS)
        if not has_service_term and line_start > 0:
            prev_end = max(0, line_start - 1)
            prev_start = max(text.rfind("\n", 0, prev_end), text.rfind("\r", 0, prev_end)) + 1
            prev_line = re.sub(r"\s+", " ", text[prev_start:prev_end]).strip()
            if prev_line and len(prev_line) <= 120:
                return f"{prev_line} {line}".strip()
        return line

    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _keyword_terms_for_price(keywords: list[str] | None) -> list[str]:
    terms: list[str] = []
    for keyword in keywords or []:
        compact = _compact(keyword)
        if not compact:
            continue
        for term in PRICE_KEYWORD_TERMS:
            if term in compact and term not in terms:
                terms.append(term)
        if not terms and compact not in PRICE_NOISE_TERMS and len(compact) >= 2:
            terms.append(compact)
    return terms


def _price_keyword_score(text: str, hit: PriceHit, terms: list[str]) -> int:
    compact_context = _compact(hit.context)
    context_score = sum(1 for term in terms if term in compact_context)
    proximity_score = 0
    lower_text = text.lower()
    for term in terms:
        if term in GENERIC_PRICE_TERMS:
            continue
        positions = [m.start() for m in re.finditer(re.escape(term), lower_text)]
        if not positions:
            continue
        distance = min(abs(pos - hit.start) for pos in positions)
        if distance <= 120:
            proximity_score += 120 - distance
    return context_score * 10 + proximity_score


def extract_price_hits(text: str) -> list[PriceHit]:
    """본문에서 원화 단가 후보를 모두 추출."""
    hits: list[PriceHit] = []
    if not text:
        return hits
    for m in PRICE_RE.finditer(text):
        try:
            amount = _price_amount(m.group("number"), m.group("unit"))
        except ValueError:
            continue
        if amount <= 0 or amount > 100_000_000:
            continue
        raw = m.group(0).strip()
        hits.append(
            PriceHit(
                amount=amount,
                raw=raw,
                context=_price_context(text, m.start(), m.end()),
                start=m.start(),
            )
        )
    return hits


def extract_min_price(text: str, keywords: list[str] | None = None) -> tuple[int | None, str]:
    """키워드 주변 단가를 우선 보고, 없으면 본문 전체 최저 단가를 반환."""
    hits = extract_price_hits(text)
    if not hits:
        return None, ""

    terms = _keyword_terms_for_price(keywords)
    selected = hits
    if terms:
        scored = [(_price_keyword_score(text, hit, terms), hit) for hit in hits]
        best_score = max(score for score, _ in scored)
        if best_score > 0:
            selected = [hit for score, hit in scored if score == best_score]

    best = min(selected, key=lambda hit: hit.amount)
    return best.amount, best.context


def format_price(amount: int | None) -> str:
    return f"{amount:,}원" if amount else ""


def extract_contacts(body_text: str) -> ContactInfo:
    """본문 plain text 에서 연락 정보 모두 추출."""
    info = ContactInfo()

    # 오픈채팅 (먼저 처리해서 일반 URL 추출과 안 겹치게)
    for url in OPENCHAT_RE.findall(body_text):
        if url not in info.open_chat_urls:
            info.open_chat_urls.append(url)

    # 카톡 ID
    for m in KAKAO_KW_RE.finditer(body_text):
        kid = m.group(1).strip().rstrip(".,;:")
        # 너무 짧거나 의미없는 단어 필터
        if len(kid) < 3 or kid.lower() in KAKAO_BLOCKLIST:
            continue
        # @something 뒤에 카톡이라고 적힌 경우(이메일 일부) 거르기
        prev_char = body_text[max(0, m.start(1) - 1):m.start(1)]
        if prev_char == "@":
            continue
        # URL (https://...) 의 일부를 잘못 잡은 경우 제외
        after = body_text[m.end(1):m.end(1) + 3]
        if after.startswith("://") or after.startswith("//"):
            continue
        if kid not in info.kakao_ids:
            info.kakao_ids.append(kid)

    # 전화/핸드폰
    seen_phone = set()
    for m in PHONE_RE.finditer(body_text):
        norm = normalize_phone(m.group(0))
        if norm and norm not in seen_phone:
            seen_phone.add(norm)
            info.phones.append(norm)

    # 이메일
    seen_email = set()
    for m in EMAIL_RE.finditer(body_text):
        em = m.group(0).lower()
        if em not in seen_email:
            seen_email.add(em)
            info.emails.append(em)

    # 회사/상호명
    cm = COMPANY_RE.search(body_text)
    if cm:
        info.company = cm.group(1).strip()

    return info


def decode_b64_email(b64: str) -> str:
    """셀클럽 showSideView 의 base64 이메일 디코드 (실패 시 빈 문자열)."""
    try:
        return base64.b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        return ""


def merge_contacts(*infos: ContactInfo) -> ContactInfo:
    """여러 ContactInfo 합치기 (본문 + 메타 영역 등 복수 소스)."""
    out = ContactInfo()
    for info in infos:
        if not info:
            continue
        for k in info.kakao_ids:
            if k not in out.kakao_ids:
                out.kakao_ids.append(k)
        for u in info.open_chat_urls:
            if u not in out.open_chat_urls:
                out.open_chat_urls.append(u)
        for p in info.phones:
            if p not in out.phones:
                out.phones.append(p)
        for e in info.emails:
            if e not in out.emails:
                out.emails.append(e)
        if not out.company and info.company:
            out.company = info.company
    return out


if __name__ == "__main__":
    sample = """
    안녕하세요 인스타 마케팅 대행사입니다.
    상호: ABC마케팅
    문의는 카톡 ID: marketing_pro123 또는 010-1234-5678 로 주세요.
    이메일 contact@example.com
    오픈채팅 https://open.kakao.com/o/abcDEF1
    """
    info = extract_contacts(sample)
    print(info)
