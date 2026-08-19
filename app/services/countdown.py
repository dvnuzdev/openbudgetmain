from datetime import datetime, timezone, timedelta
from typing import Tuple
from app.services.emoji_manager import emoji_manager

# Uzbekistan Timezone GMT+5
UZB_TZ = timezone(timedelta(hours=5))
TARGET_DEADLINE = datetime(2026, 8, 22, 0, 0, 0, tzinfo=UZB_TZ)

def get_countdown_info() -> Tuple[str, str, bool]:
    """
    Calculates remaining time until August 22, 00:00 GMT+5.
    Returns: (countdown_str: str, formatted_html_msg: str, is_started: bool)
    """
    now = datetime.now(UZB_TZ)
    diff = TARGET_DEADLINE - now

    e_timer = emoji_manager.get("timer")
    e_cal = emoji_manager.get("calendar")
    e_build = emoji_manager.get("building")
    e_spk = emoji_manager.get("speaker")
    e_bal = emoji_manager.get("balance")

    if diff.total_seconds() <= 0:
        return "0 kun, 00 soat, 00 daqiqa", f"🚀 <b>OPENBUDGET OVOZ BERISH MAVSUMI RASMAN BOSHLANDI!</b>", True

    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    countdown_str = f"{days} kun, {hours:02d} soat, {minutes:02d} daqiqa"

    formatted_html = (
        f"{e_timer} <b>OPENBUDGET MAVSUMI HALI BOSHLANMADI!</b>\n\n"
        f"<blockquote>{e_cal} <b>Ovoz berish boshlanishiga:</b> <code>{countdown_str}</code> qoldi!\n\n"
        f"{e_spk} <b>ESLATMA:</b> OpenBudget mavsumi rasman boshlanishi bilan botimiz avtomatik ishga tushadi va ovozlarni qabul qila boshlaydi.\n\n"
        f"{e_build} Loyihamizni qo'llab-quvvatlashga tayyor turing!\n"
        f"{e_bal} Boshlanishi bilan ovoz bering va pul mukofotini kartangizga oling!</blockquote>"
    )

    return countdown_str, formatted_html, False

def get_countdown_text() -> Tuple[str, bool]:
    """HTML format helper for group broadcasts."""
    c_str, html_msg, is_started = get_countdown_info()
    return html_msg, is_started
