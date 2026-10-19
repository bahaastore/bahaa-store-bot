"""إعدادات بهاء ستور — تُقرأ من ملف .env أو من متغيرات البيئة."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# ── تيليجرام ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# معرّفات المدراء (أرقام تيليجرام) مفصولة بفواصل
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
# الرابط العام (HTTPS) الذي يعمل عليه الخادم — يُستخدم لفتح التطبيق المصغّر
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# ── المزوّد (ماكس ستور) ─────────────────────────────────
SUPPLIER_BASE_URL = os.getenv("SUPPLIER_BASE_URL", "https://maxstore1.com").rstrip("/")
SUPPLIER_API_TOKEN = os.getenv("SUPPLIER_API_TOKEN", "")
# طريقة تسمية حقول الطلب: snake = "Player ID" -> "player_id" (كما في التوثيق) | raw = كما هي
SUPPLIER_PARAM_KEYS = os.getenv("SUPPLIER_PARAM_KEYS", "snake")

# ── المتجر ───────────────────────────────────────────────
STORE_NAME = os.getenv("STORE_NAME", "بهاء ستور")
DEFAULT_MARKUP_PCT = float(os.getenv("MARKUP_PCT", "30"))
DEFAULT_SYP_RATE = float(os.getenv("SYP_RATE", "13000"))
SUPPORT_URL = os.getenv("SUPPORT_URL", "")

# ── التشغيل ─────────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
# قاعدة بيانات دائمة (PostgreSQL) — إن وُجدت تُستخدم بدل SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "store.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
# وضع التجربة: يسمح بفتح التطبيق من المتصفح بدون تيليجرام ويستخدم مزوّداً وهمياً إن لم يوجد توكن
DEMO_MODE = _bool("DEMO_MODE", False)
RUN_BOT = _bool("RUN_BOT", True)
CATALOG_REFRESH_SEC = int(os.getenv("CATALOG_REFRESH_SEC", "600"))
ORDER_POLL_SEC = int(os.getenv("ORDER_POLL_SEC", "20"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
