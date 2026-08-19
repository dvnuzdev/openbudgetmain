import logging
import html
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.asyncio import Redis

from app.database.models import Vote, VoteStatus, User, PayoutTicket, TicketStatus, PayoutType
from app.bot.states import VoteStates, PayoutStates
from app.bot.keyboards.reply import get_phone_request_keyboard, get_main_menu_keyboard, get_cancel_keyboard
from app.bot.keyboards.inline import get_payout_choice_keyboard
from app.services.anti_fraud import clean_phone_number, is_valid_uzbek_phone, is_valid_card_number
from app.services.payout_service import create_payout_ticket
from app.services.emoji_manager import emoji_manager
from app.config import settings

logger = logging.getLogger(__name__)
router = Router()

def check_is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list

def get_openbudget_site_keyboard() -> InlineKeyboardMarkup:
    """Returns inline keyboard linking to official OpenBudget voting portal."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 OpenBudget Saytiga O'tish",
                    style="success",
                    url="https://openbudget.uz/board-initiatives"
                )
            ]
        ]
    )

@router.message(F.text.contains("Bekor qilish"))
async def cancel_handler(message: Message, state: FSMContext):
    is_adm = check_is_admin(message.from_user.id)
    await state.clear()
    cancel_text = f"{emoji_manager.get('cancel')} <b>Jarayon bekor qilindi.</b>\n\n{emoji_manager.get('finger_down')} Menyudan bo'limni tanlang:"
    await message.answer(cancel_text, reply_markup=get_main_menu_keyboard(is_admin=is_adm), parse_mode="HTML")

@router.message(F.text.contains("Ovoz berish"), F.chat.type == "private")
@router.message(F.text.contains("Boshqa raqamdan ovoz"), F.chat.type == "private")
async def start_website_vote_prompt(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(VoteStates.waiting_for_phone)

    text = (
        f"{emoji_manager.get('vote')} <b>OPENBUDGET SAYTIDA OVOZ BERISH:</b>\n\n"
        f"1️⃣ Pastdagi <b>🌐 OpenBudget Saytiga O'tish</b> tugmasini bosing va loyihaga ovoz bering.\n"
        f"2️⃣ Saytdan ovoz berib bo'lgach, ovoz bergan <b>telefon raqamingizni</b> shu botga yuboring!\n\n"
        f"<i>(Ovozingiz adminlarimiz tomonidan tekshirilib, pulingiz kartangizga o'tkazib beriladi)</i>\n\n"
        f"👇 <b>Ovoz bergan telefon raqamingizni yozing (Masalan: +998901234567):</b>"
    )
    await message.answer(
        text,
        reply_markup=get_openbudget_site_keyboard(),
        parse_mode="HTML"
    )

@router.message(VoteStates.waiting_for_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext, session: AsyncSession):
    contact = message.contact
    raw_phone = contact.phone_number
    await handle_voted_phone_submission(message, state, session, raw_phone)

@router.message(VoteStates.waiting_for_phone, F.text)
async def process_phone_text(message: Message, state: FSMContext, session: AsyncSession):
    raw_phone = message.text.strip()
    await handle_voted_phone_submission(message, state, session, raw_phone)

async def handle_voted_phone_submission(message: Message, state: FSMContext, session: AsyncSession, raw_phone: str):
    normalized_phone = clean_phone_number(raw_phone)
    is_adm = check_is_admin(message.from_user.id)

    if not is_valid_uzbek_phone(normalized_phone):
        await message.answer(
            f"{emoji_manager.get('warning')} <b>Telefon raqami noto'g'ri kiritildi!</b>\n"
            "Iltimos, <code>+998XXXXXXXXX</code> formatidagi raqamni yuboring:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    # Check if this phone has already been submitted in tickets
    stmt = select(PayoutTicket).where(PayoutTicket.destination.contains(normalized_phone))
    res = await session.execute(stmt)
    existing_ticket = res.scalar_one_or_none()

    if existing_ticket and existing_ticket.status in [TicketStatus.PAID, TicketStatus.PENDING]:
        await message.answer(
            f"{emoji_manager.get('danger')} <b>Ushbu raqam ({normalized_phone}) bo'yicha allaqachon zayavka mavjud!</b>\n"
            f"Bitta raqamdan faqat 1 marta ovoz berib pul olish mumkin.",
            reply_markup=get_main_menu_keyboard(is_admin=is_adm),
            parse_mode="HTML"
        )
        await state.clear()
        return

    await state.update_data(voted_phone=normalized_phone)
    await state.set_state(PayoutStates.waiting_for_card)

    await message.answer(
        f"{emoji_manager.get('balance')} <b>Pul o'tkazib beriladigan karta yoki telefon raqamingizni kiriting:</b>\n\n"
        f"Format: <code>8600123456789012</code> (Karta) yoki <code>+998901234567</code> (Paynet)\n"
        f"Mukofot summa: <b>{settings.DEFAULT_REWARD_PER_VOTE:,} UZS</b>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(PayoutStates.waiting_for_card, F.chat.type == "private")
async def process_payout_dest_input(message: Message, state: FSMContext, session: AsyncSession, bot_identifier: str = "bot1"):
    raw_dest = message.text.strip() if message.text else ""
    clean_dest = raw_dest.replace(" ", "")
    user_id = message.from_user.id
    is_adm = check_is_admin(user_id)

    p_type = PayoutType.CARD if len(clean_dest) == 16 and clean_dest.isdigit() else PayoutType.PHONE

    if p_type == PayoutType.CARD:
        if not is_valid_card_number(clean_dest):
            await message.answer(
                f"{emoji_manager.get('warning')} <b>Karta raqami noto'g'ri kiritildi!</b>\n"
                "Iltimos, 16 ta raqamdan iborat Uzcard/Humo karta yuboring:",
                reply_markup=get_cancel_keyboard(),
                parse_mode="HTML"
            )
            return

        await state.update_data(clean_card_number=clean_dest)
        await state.set_state(PayoutStates.waiting_for_card_holder_name)

        await message.answer(
            f"{emoji_manager.get('user_admin')} <b>Karta egasining Ismi va Familiyasini kiriting:</b>\n\n"
            "<i>Iltimos, kartangiz ustidagi ism va familiyangizni yozing (masalan: ALISHER NAVOIY):</i>",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
    else:
        norm_phone = clean_phone_number(clean_dest)
        if not is_valid_uzbek_phone(norm_phone):
            await message.answer(
                f"{emoji_manager.get('warning')} <b>To'lov rekviziti noto'g'ri kiritildi!</b>\n"
                "16 ta raqamli Karta yoki <code>+998XXXXXXXXX</code> formatdagi telefon raqam yuboring:",
                reply_markup=get_cancel_keyboard(),
                parse_mode="HTML"
            )
            return

        data = await state.get_data()
        voted_phone = data.get("voted_phone")

        success, msg, ticket = await create_payout_ticket(
            session=session,
            telegram_id=user_id,
            vote_id=None,
            payout_type=PayoutType.PHONE,
            destination=f"{voted_phone} -> Paynet: {norm_phone}",
            bot=message.bot,
            bot_identifier=bot_identifier
        )

        await state.clear()

        if not success or not ticket:
            await message.answer(f"{emoji_manager.get('danger')} {msg}", reply_markup=get_main_menu_keyboard(is_admin=is_adm), parse_mode="HTML")
            return

        user_ack = (
            f"{emoji_manager.get('success')} <b>ZAYAVKA MUVAFFAQIYATLI YARATILDI!</b>\n\n"
            f"{emoji_manager.get('tickets_icon')} Zayavka kodi: <code>{ticket.ticket_code}</code>\n"
            f"{emoji_manager.get('other_phone')} Ovoz berilgan raqam: <code>{voted_phone}</code>\n"
            f"{emoji_manager.get('balance')} To'lov raqami: <code>{norm_phone}</code>\n"
            f"{emoji_manager.get('warning')} Holat: <b>Ko'rib chiqilmoqda (Pending)</b>\n\n"
            f"Adminlarimiz saytdan ovozingizni tekshirib, to'lovni amalga oshirgach bildirishnoma keladi."
        )
        await message.answer(user_ack, reply_markup=get_main_menu_keyboard(is_admin=is_adm), parse_mode="HTML")

@router.message(PayoutStates.waiting_for_card_holder_name, F.chat.type == "private")
async def process_card_holder_name_input(message: Message, state: FSMContext, session: AsyncSession, bot_identifier: str = "bot1"):
    holder_name = message.text.strip() if message.text else ""
    user_id = message.from_user.id
    is_adm = check_is_admin(user_id)

    if len(holder_name) < 3:
        await message.answer(f"{emoji_manager.get('warning')} Iltimos, karta egasining to'liq ism va familiyasini kiriting (masalan: ALISHER NAVOIY):", reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        return

    data = await state.get_data()
    card_clean = data.get("clean_card_number")
    voted_phone = data.get("voted_phone")

    success, msg, ticket = await create_payout_ticket(
        session=session,
        telegram_id=user_id,
        vote_id=None,
        payout_type=PayoutType.CARD,
        destination=f"{voted_phone} -> Karta: {card_clean}",
        card_holder_name=holder_name,
        bot=message.bot,
        bot_identifier=bot_identifier
    )

    await state.clear()

    if not success or not ticket:
        await message.answer(f"{emoji_manager.get('danger')} {msg}", reply_markup=get_main_menu_keyboard(is_admin=is_adm), parse_mode="HTML")
        return

    safe_holder = html.escape(holder_name)
    user_ack = (
        f"{emoji_manager.get('success')} <b>ZAYAVKA MUVAFFAQIYATLI YARATILDI!</b>\n\n"
        f"{emoji_manager.get('tickets_icon')} Zayavka kodi: <code>{ticket.ticket_code}</code>\n"
        f"{emoji_manager.get('other_phone')} Ovoz berilgan raqam: <code>{voted_phone}</code>\n"
        f"{emoji_manager.get('balance')} Karta raqam: <code>{card_clean}</code>\n"
        f"{emoji_manager.get('user_admin')} Karta egasi: <b>{safe_holder}</b>\n"
        f"{emoji_manager.get('warning')} Holat: <b>Ko'rib chiqilmoqda (Pending)</b>\n\n"
        f"Adminlarimiz saytdan ovozingizni tekshirib, to'lovni amalga oshirgach bildirishnoma keladi."
    )
    await message.answer(
        user_ack,
        reply_markup=get_main_menu_keyboard(is_admin=is_adm),
        parse_mode="HTML"
    )
