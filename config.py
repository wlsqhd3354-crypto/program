"""전역 설정값. 시트 URL, 셀클럽 URL 등."""

APP_VERSION = "1.3.15"
UPDATE_INFO_URL = "https://github.com/wlsqhd3354-crypto/program/releases/latest/download/version.json"

GSHEET_ID = "1yThLpfi7r0ApHWSV6Qj6lou8Uq5Zy__jpLEdAXekXIQ"
GSHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{GSHEET_ID}/export?format=csv"

SELLCLUB_BASE = "http://www.sellclub.kr"
SELLCLUB_LOGIN_URL = f"{SELLCLUB_BASE}/community/bbs/login.php"
SELLCLUB_LOGIN_CHECK_URL = f"{SELLCLUB_BASE}/community/bbs/login_check.php"
SELLCLUB_BOARD = "maket_5_3"
SELLCLUB_WRITE_URL = f"{SELLCLUB_BASE}/community/bbs/write.php?bo_table={SELLCLUB_BOARD}"
SELLCLUB_WRITE_UPDATE_URL = f"{SELLCLUB_BASE}/community/bbs/write_update.php"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

DEFAULT_INTERVAL_SEC = 300
DEFAULT_JITTER_SEC = 60
DEFAULT_REPEAT_COUNT = 10

MESSAGES_DIR = "messages"
IMAGES_DIR = "images"
