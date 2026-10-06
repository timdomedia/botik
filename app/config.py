import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    bot_token: str
    chat_id: int
    tilda_token: str
    port: int
    acquiring_percent: float
    dane_share_percent: float
    ship_deadline_days: int
    reminder_hour: int | None
    sheet_id: str
    service_account_file: str
    db_path: str
    products_file: str


def load_settings() -> Settings:
    _load_dotenv()
    reminder = os.getenv("REMINDER_HOUR", "").strip()
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        chat_id=int(os.environ["CHAT_ID"]),
        tilda_token=os.getenv("TILDA_TOKEN", ""),
        port=int(os.getenv("PORT", "8080")),
        acquiring_percent=float(os.getenv("ACQUIRING_PERCENT", "0")),
        dane_share_percent=float(os.getenv("DANE_SHARE_PERCENT", "50")),
        ship_deadline_days=int(os.getenv("SHIP_DEADLINE_DAYS", "3")),
        reminder_hour=int(reminder) if reminder else None,
        sheet_id=os.getenv("GOOGLE_SHEET_ID", "").strip(),
        service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        db_path=os.getenv("DB_PATH", "data/orders.db"),
        products_file=os.getenv("PRODUCTS_FILE", "products.yaml"),
    )
