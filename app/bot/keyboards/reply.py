from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from app.services.emoji_manager import emoji_manager

def get_main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Returns main menu reply keyboard with Left buttons = Green (success), Right buttons = Blue (primary)."""
    def make_btn(text: str, key: str, style: str = None) -> KeyboardButton:
        kwargs = {"text": text}
        if style:
            kwargs["style"] = style
        emoji_id = emoji_manager.get_emoji_id(key)
        if emoji_id:
            kwargs["icon_custom_emoji_id"] = emoji_id
        return KeyboardButton(**kwargs)

    # Left side = green ("success"), Right side = blue ("primary")
    buttons = [
        [
            make_btn("Ovoz berish", "vote", style="success"),
            make_btn("Boshqa raqamdan ovoz", "other_phone", style="primary")
        ],
        [
            make_btn("Statistikam", "votes_icon", style="success"),
            make_btn("To'lov holati", "balance", style="primary")
        ],
        [
            make_btn("To'lovlar kanali", "channel", style="success"),
            make_btn("Mening havolam", "link", style="primary")
        ],
        [
            make_btn("Top Referrallar", "top_ref", style="success"),
            make_btn("Yordam / Qoidalar", "help", style="primary")
        ]
    ]

    if is_admin:
        buttons.append([make_btn("Admin Panel", "admin", style="primary")])

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        persistent=True
    )

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Returns red cancel reply keyboard for FSM states."""
    kwargs = {"text": "Bekor qilish", "style": "danger"}
    emoji_id = emoji_manager.get_emoji_id("cancel")
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(**kwargs)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_phone_request_keyboard() -> ReplyKeyboardMarkup:
    """Returns contact share reply keyboard with styled red cancel button."""
    kwargs_cancel = {"text": "Bekor qilish", "style": "danger"}
    emoji_id = emoji_manager.get_emoji_id("cancel")
    if emoji_id:
        kwargs_cancel["icon_custom_emoji_id"] = emoji_id

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Telefon raqamni yuborish", request_contact=True, style="success")
            ],
            [
                KeyboardButton(**kwargs_cancel)
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
