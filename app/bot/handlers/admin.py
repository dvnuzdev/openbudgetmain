import logging
import html
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from redis.asyncio import Redis

from app.database.models import PayoutTicket, Vote, VoteStatus, TicketStatus, SystemBudget, Group, User
from app.services.payout_service import process_ticket_action, get_or_create_budget, mask_destination
from app.services.emoji_manager import emoji_manager, EMOJI_LABELS
from app.bot.keyboards.inline import (
    get_admin_dashboard_keyboard,
    get_admin_ticket_keyboard,
    get_admin_reject_reasons_keyboard,
    get_payout_proof_channel_keyboard,
    get_admin_emojis_keyboard
)
from app.bot.handlers.group import get_group_promo_keyboard
from app.config import settings

logger = logging.getLogger(__name__)
router = Router()

class AdminBroadcastState(StatesGroup):
    waiting_for_ad_text = State()

class AdminReceiptState(StatesGroup):
    waiting_for_photo = State()

class AdminProjectIDState(StatesGroup):
    waiting_for_project_id = State()

class AdminVotePriceState(StatesGroup):
    waiting_for_price = State()

class AdminRefBonusState(StatesGroup):
    waiting_for_ref_bonus = State()

class AdminVoteAdjustState(StatesGroup):
    waiting_for_offset = State()

class AdminRejectState(StatesGroup):
    waiting_for_reason = State()

class AdminEmojiState(StatesGroup):
    waiting_for_emoji = State()

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list

async def build_admin_stats_text(session: AsyncSession, redis: Redis, bot_identifier: str = "bot1") -> str:
    await emoji_manager.load_emojis(redis)

    total_users_stmt = select(func.count(User.telegram_id))
    total_users = (await session.execute(total_users_stmt)).scalar_one_or_none() or 0

    total_votes_stmt = select(func.count(Vote.id)).where(Vote.status == VoteStatus.VERIFIED)
    raw_votes = (await session.execute(total_votes_stmt)).scalar_one_or_none() or 0
    total_votes = max(0, raw_votes + settings.MANUAL_VOTE_OFFSET)

    total_pending_stmt = select(func.count(PayoutTicket.id)).where(PayoutTicket.status == TicketStatus.PENDING)
    total_pending = (await session.execute(total_pending_stmt)).scalar_one_or_none() or 0

    groups_stmt = select(func.count(Group.chat_id)).where(Group.is_active == True)
    active_groups = (await session.execute(groups_stmt)).scalar_one_or_none() or 0

    budget = await get_or_create_budget(session)

    e_admin = emoji_manager.get("admin")
    e_users = emoji_manager.get("users_icon")
    e_votes = emoji_manager.get("votes_icon")
    e_tickets = emoji_manager.get("tickets_icon")
    e_groups = emoji_manager.get("groups_icon")
    e_pin = emoji_manager.get("pin_icon")
    e_lock = emoji_manager.get("lock_icon")
    e_paid = emoji_manager.get("paid_icon")

    stats_text = (
        f"{e_admin} <b>ADMIN PANEL — BOT STATISTIKASI</b>\n\n"
        f"<blockquote>{e_users} Foydalanuvchilar: <b>{total_users}</b> ta\n"
        f"{e_votes} Ovozlar: <b>{total_votes}</b> ta\n"
        f"{e_tickets} Kutilayotgan zayavkalar: <b>{total_pending}</b> ta\n"
        f"{e_groups} Faol guruhlar: <b>{active_groups}</b> ta\n\n"
        f"{e_pin} Loyiha ID: <code>{settings.OPENBUDGET_PROJECT_ID}</code>\n"
        f"{e_paid} Ovoz Narxi: <b>{settings.DEFAULT_REWARD_PER_VOTE:,} UZS</b>\n"
        f"🎁 Referal Bonusi: <b>{settings.REFERRAL_BONUS_PER_VOTE:,} UZS</b>\n"
        f"{e_lock} Band qilingan: <b>{budget.total_reserved_uzs:,} UZS</b>\n"
        f"{e_paid} To'langan: <b>{budget.total_paid_uzs:,} UZS</b></blockquote>"
    )
    return stats_text

@router.message(Command("myid"))
async def handle_my_id_command(message: Message):
    status_str = f"{emoji_manager.get('success')} Admin" if is_admin(message.from_user.id) else f"{emoji_manager.get('user_admin')} Oddiy foydalanuvchi"
    await message.answer(
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"Maqom: {status_str}",
        parse_mode="HTML"
    )

@router.message(Command("admin"))
@router.message(F.text.contains("Admin Panel"))
async def open_admin_panel(message: Message, session: AsyncSession, redis: Redis, bot_identifier: str = "bot1"):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer(
            f"{emoji_manager.get('warning')} Siz administrator emassiz.\nID: <code>{user_id}</code>",
            parse_mode="HTML"
        )
        return

    stats_text = await build_admin_stats_text(session, redis, bot_identifier)
    await message.answer(stats_text, reply_markup=get_admin_dashboard_keyboard(), parse_mode="HTML")

@router.message(Command("tickets"))
@router.message(Command("pending"))
async def handle_pending_tickets_cmd(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    stmt = select(PayoutTicket).where(PayoutTicket.status == TicketStatus.PENDING).order_by(PayoutTicket.created_at.asc()).limit(10)
    res = await session.execute(stmt)
    tickets = res.scalars().all()

    if not tickets:
        await message.answer(f"{emoji_manager.get('success')} Kutilayotgan zayavkalar yo'q.", parse_mode="HTML")
        return

    await message.answer(f"{emoji_manager.get('warning')} <b>KUTILAYOTGAN ZAYAVKALAR:</b>", parse_mode="HTML")

    for t in tickets:
        user_stmt = select(User).where(User.telegram_id == t.telegram_id)
        u_res = await session.execute(user_stmt)
        user_obj = u_res.scalar_one_or_none()

        raw_name = user_obj.full_name if (user_obj and user_obj.full_name) else "User"
        safe_name = html.escape(raw_name)
        user_mention = f'<a href="tg://user?id={t.telegram_id}">{safe_name}</a>'
        user_uname = f"@{user_obj.username}" if (user_obj and user_obj.username) else "Yo'q"
        p_type = t.payout_type.value if hasattr(t.payout_type, 'value') else t.payout_type
        holder = f"\n{emoji_manager.get('user_admin')} Egasi: <b>{html.escape(t.card_holder_name)}</b>" if t.card_holder_name else ""

        ticket_info = (
            f"{emoji_manager.get('tickets_icon')} <b>#{t.ticket_code}</b>\n"
            f"{emoji_manager.get('user_admin')} User: {user_mention} ({user_uname} | <code>{t.telegram_id}</code>)\n"
            f"{emoji_manager.get('balance')} Rekvizit: <code>{t.destination}</code> ({p_type}){holder}\n"
            f"{emoji_manager.get('paid_icon')} Summa: <b>{t.amount_uzs:,} UZS</b>\n"
            f"{emoji_manager.get('calendar')} Sana: {t.created_at.strftime('%m-%d %H:%M')}"
        )
        await message.answer(ticket_info, reply_markup=get_admin_ticket_keyboard(t.id), parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_upload_receipt:"))
async def handle_admin_upload_receipt_click(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⚠️ Siz admin emassiz!", show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[1])
    await state.update_data(receipt_target_ticket_id=ticket_id)
    await state.set_state(AdminReceiptState.waiting_for_photo)

    await callback.message.reply(f"{emoji_manager.get('tickets_icon')} Chek rasmini yuboring (/cancel - bekor qilish):", parse_mode="HTML")
    await callback.answer()

@router.message(AdminReceiptState.waiting_for_photo, F.photo)
async def process_admin_receipt_photo_upload(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    ticket_id = data.get("receipt_target_ticket_id")

    stmt = select(PayoutTicket).where(PayoutTicket.id == ticket_id)
    res = await session.execute(stmt)
    ticket = res.scalar_one_or_none()

    if not ticket:
        await message.answer(f"{emoji_manager.get('danger')} Zayavka topilmadi.", parse_mode="HTML")
        await state.clear()
        return

    photo_file_id = message.photo[-1].file_id

    try:
        user_caption = (
            f"{emoji_manager.get('success')} <b>TO'LOV CHEKI KELDI!</b>\n\n"
            f"{emoji_manager.get('tickets_icon')} Kodi: <code>{ticket.ticket_code}</code>\n"
            f"{emoji_manager.get('balance')} Rekvizit: <code>{ticket.destination}</code>\n"
            f"{emoji_manager.get('paid_icon')} Admin to'lov chekini tasdiqladi."
        )
        await message.bot.send_photo(
            chat_id=ticket.telegram_id,
            photo=photo_file_id,
            caption=user_caption,
            reply_markup=get_payout_proof_channel_keyboard(),
            parse_mode="HTML"
        )
        await message.answer(f"{emoji_manager.get('success')} Chek foydalanuvchiga yuborildi (ID: <code>{ticket.telegram_id}</code>).", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send receipt photo to user {ticket.telegram_id}: {e}")
        await message.answer(f"{emoji_manager.get('danger')} Xatolik: {e}", parse_mode="HTML")

    await state.clear()

@router.callback_query(F.data.startswith("admin_menu:"))
async def handle_admin_dashboard_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession, redis: Redis, bot_identifier: str = "bot1"):
    if not is_admin(callback.from_user.id):
        await callback.answer("⚠️ Siz admin emassiz!", show_alert=True)
        return

    action = callback.data.split(":")[1]

    if action in ["stats", "refresh"]:
        stats_text = await build_admin_stats_text(session, redis, bot_identifier)
        await callback.message.edit_text(stats_text, reply_markup=get_admin_dashboard_keyboard(), parse_mode="HTML")
        await callback.answer("Yangilandi!")

    elif action == "users_stats":
        stmt = (
            select(
                User.telegram_id,
                User.full_name,
                User.username,
                func.count(Vote.id).label("vote_count")
            )
            .outerjoin(Vote, (Vote.telegram_id == User.telegram_id) & (Vote.status == VoteStatus.VERIFIED))
            .group_by(User.telegram_id, User.full_name, User.username)
            .order_by(desc("vote_count"), User.telegram_id)
        )
        res = await session.execute(stmt)
        users_votes = res.all()

        if not users_votes:
            text = f"{emoji_manager.get('votes_icon')} <b>FOYDALANUVCHILAR OVOZLAR STATISTIKASI:</b>\n\n<i>Foydalanuvchilar topilmadi.</i>"
        else:
            text = f"{emoji_manager.get('votes_icon')} <b>FOYDALANUVCHILAR OVOZLAR STATISTIKASI:</b>\n\n"
            for idx, row in enumerate(users_votes, 1):
                name = html.escape(row.full_name or f"User_{row.telegram_id}")
                text += f"{idx}. <b>{name}</b> (<code>{row.telegram_id}</code>) — <b>{row.vote_count} ta</b> ovoz\n"

        await callback.message.edit_text(text, reply_markup=get_admin_dashboard_keyboard(), parse_mode="HTML")
        await callback.answer()

    elif action == "emojis":
        await callback.message.edit_text(
            f"{emoji_manager.get('welcome')} <b>PREMIUM EMOJILAR SOZLAMASI:</b>\n\n"
            "O'zgartirmoqchi bo'lgan element emojisini tanlang:",
            reply_markup=get_admin_emojis_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "change_project_id":
        await state.set_state(AdminProjectIDState.waiting_for_project_id)
        await callback.message.answer(
            f"{emoji_manager.get('pin_icon')} <b>OpenBudget Loyiha ID sini kiriting:</b>\n"
            f"Hozirgi ID: <code>{settings.OPENBUDGET_PROJECT_ID}</code>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "change_vote_price":
        await state.set_state(AdminVotePriceState.waiting_for_price)
        await callback.message.answer(
            f"💵 <b>Har bir ovoz uchun mukofot narxini kiriting (UZS):</b>\n"
            f"Hozirgi narx: <b>{settings.DEFAULT_REWARD_PER_VOTE:,} UZS</b>\n\n"
            f"<i>(Masalan: 25000 deb yozing)</i>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "change_ref_bonus":
        await state.set_state(AdminRefBonusState.waiting_for_ref_bonus)
        await callback.message.answer(
            f"🎁 <b>Har bir tasdiqlangan ovoz uchun Referal Bonus summasini kiriting (UZS):</b>\n"
            f"Hozirgi bonus: <b>{settings.REFERRAL_BONUS_PER_VOTE:,} UZS</b>\n\n"
            f"<i>(Masalan: 5000 deb yozing, o'chirish uchun 0 kiriting)</i>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "adjust_votes":
        await state.set_state(AdminVoteAdjustState.waiting_for_offset)
        await callback.message.answer(
            f"📊 <b>Ovozlar statistikasi soniga qo'shish yoki ayirish:</b>\n"
            f"Hozirgi qo'shimcha offset: <b>{settings.MANUAL_VOTE_OFFSET:+d}</b>\n\n"
            f"<i>(Masalan: +50 yoki -10 deb yozing)</i>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "pending":
        stmt = select(PayoutTicket).where(PayoutTicket.status == TicketStatus.PENDING).order_by(PayoutTicket.created_at.asc()).limit(10)
        res = await session.execute(stmt)
        tickets = res.scalars().all()

        if not tickets:
            await callback.answer("✅ Kutilayotgan zayavkalar yo'q!", show_alert=True)
            return

        await callback.message.answer(f"{emoji_manager.get('warning')} <b>KUTILAYOTGAN ZAYAVKALAR:</b>", parse_mode="HTML")

        for t in tickets:
            user_stmt = select(User).where(User.telegram_id == t.telegram_id)
            u_res = await session.execute(user_stmt)
            user_obj = u_res.scalar_one_or_none()

            raw_name = user_obj.full_name if (user_obj and user_obj.full_name) else "User"
            safe_name = html.escape(raw_name)
            user_mention = f'<a href="tg://user?id={t.telegram_id}">{safe_name}</a>'
            user_uname = f"@{user_obj.username}" if (user_obj and user_obj.username) else "Yo'q"
            p_type = t.payout_type.value if hasattr(t.payout_type, 'value') else t.payout_type
            holder = f"\n{emoji_manager.get('user_admin')} Egasi: <b>{html.escape(t.card_holder_name)}</b>" if t.card_holder_name else ""

            ticket_info = (
                f"{emoji_manager.get('tickets_icon')} <b>#{t.ticket_code}</b>\n"
                f"{emoji_manager.get('user_admin')} User: {user_mention} ({user_uname} | <code>{t.telegram_id}</code>)\n"
                f"{emoji_manager.get('balance')} Rekvizit: <code>{t.destination}</code> ({p_type}){holder}\n"
                f"{emoji_manager.get('paid_icon')} Summa: <b>{t.amount_uzs:,} UZS</b>\n"
                f"{emoji_manager.get('calendar')} Sana: {t.created_at.strftime('%m-%d %H:%M')}"
            )
            await callback.message.answer(ticket_info, reply_markup=get_admin_ticket_keyboard(t.id), parse_mode="HTML")

        await callback.answer("Zayavkalar ko'rsatildi!")

    elif action == "groups":
        stmt_all = select(Group).order_by(Group.added_at.desc()).limit(20)
        res_all = await session.execute(stmt_all)
        all_groups = res_all.scalars().all()

        active_groups = [g for g in all_groups if g.is_active]
        inactive_groups = [g for g in all_groups if not g.is_active]

        if not all_groups:
            text = f"{emoji_manager.get('groups_icon')} <b>Guruhlar hali qo'shilmagan.</b>"
            await callback.message.edit_text(text, reply_markup=get_admin_dashboard_keyboard(), parse_mode="HTML")
            await callback.answer()
            return

        text = f"{emoji_manager.get('groups_icon')} <b>GURUHLAR:</b>\n{emoji_manager.get('success')} Faol: {len(active_groups)} ta | {emoji_manager.get('danger')} Nofaol: {len(inactive_groups)} ta\n\n"
        for idx, g in enumerate(active_groups, 1):
            text += f"{idx}. <b>{html.escape(g.title or 'Guruh')}</b> (<code>{g.chat_id}</code>)\n"

        await callback.message.edit_text(text, reply_markup=get_admin_dashboard_keyboard(), parse_mode="HTML")
        await callback.answer()

    elif action == "top_ref":
        stmt = select(User).order_by(desc(User.referral_count)).limit(10)
        res = await session.execute(stmt)
        top_users = res.scalars().all()

        if not top_users:
            await callback.answer("Referrallar topilmadi.", show_alert=True)
            return

        text = f"{emoji_manager.get('top_ref')} <b>TOP REFERRALLAR:</b>\n\n"
        for idx, u in enumerate(top_users, 1):
            name = html.escape(u.full_name or f"User_{u.telegram_id}")
            text += f"{idx}. <b>{name}</b> — {u.referral_count} ta taklif\n"

        await callback.message.edit_text(text, reply_markup=get_admin_dashboard_keyboard(), parse_mode="HTML")
        await callback.answer()

    elif action == "broadcast":
        await state.set_state(AdminBroadcastState.waiting_for_ad_text)
        default_promo = (
            f"{emoji_manager.get('welcome')} <b>OPENBUDGET RASMIY BOTI</b>\n\n"
            f"O'z raqamingiz va yaqinlaringiz raqamidan ovoz bering hamda pul mukofotini oling!\n\n"
            f"🔗 <b>Referal tizimi:</b> Do'stlaringizni taklif qiling va ular bergan har bir ovoz uchun doimiy daromad ishlang!\n\n"
            f"{emoji_manager.get('finger_down')} <b>Pastdagi tugmani bosib hoziroq boshlang:</b>"
        )
        await callback.message.answer(
            f"{emoji_manager.get('speaker')} Guruhlarga reklama matnini yuboring:\n"
            f"<i>(Standart minimalist reklamani yuborish uchun <code>1</code> deb yozing)</i>\n\n"
            f"<b>Standart reklama namunasi:</b>\n{default_promo}",
            parse_mode="HTML"
        )
        await callback.answer()

@router.message(AdminVotePriceState.waiting_for_price, F.text)
async def process_admin_change_vote_price(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text_val = message.text.strip()
    if text_val == "/cancel":
        await state.clear()
        await message.answer("Bekor qilindi.", parse_mode="HTML")
        return

    if not text_val.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat raqam kiriting (masalan: 25000):", parse_mode="HTML")
        return

    new_price = int(text_val)
    settings.DEFAULT_REWARD_PER_VOTE = new_price
    await state.clear()
    await message.answer(
        f"{emoji_manager.get('success')} <b>Ovoz narxi muvaffaqiyatli saqlandi: {new_price:,} UZS</b>",
        reply_markup=get_admin_dashboard_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminRefBonusState.waiting_for_ref_bonus, F.text)
async def process_admin_change_ref_bonus(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text_val = message.text.strip()
    if text_val == "/cancel":
        await state.clear()
        await message.answer("Bekor qilindi.", parse_mode="HTML")
        return

    if not text_val.isdigit():
        await message.answer("⚠️ Iltimos, faqat musbat raqam kiriting (masalan: 5000):", parse_mode="HTML")
        return

    new_bonus = int(text_val)
    settings.REFERRAL_BONUS_PER_VOTE = new_bonus
    await state.clear()
    await message.answer(
        f"{emoji_manager.get('success')} <b>Referal bonus muvaffaqiyatli saqlandi: {new_bonus:,} UZS</b>",
        reply_markup=get_admin_dashboard_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminVoteAdjustState.waiting_for_offset, F.text)
async def process_admin_adjust_votes(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text_val = message.text.strip()
    if text_val == "/cancel":
        await state.clear()
        await message.answer("Bekor qilindi.", parse_mode="HTML")
        return

    try:
        val = int(text_val)
        settings.MANUAL_VOTE_OFFSET += val
        await state.clear()
        await message.answer(
            f"{emoji_manager.get('success')} <b>Ovozlar statistikasi o'zgartirildi ({val:+d})!</b>\nJami qo'shimcha offset: <b>{settings.MANUAL_VOTE_OFFSET:+d}</b>",
            reply_markup=get_admin_dashboard_keyboard(),
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("⚠️ Iltimos, musbat yoki manfiy butun raqam kiriting (masalan: +50 yoki -10):", parse_mode="HTML")

@router.callback_query(F.data.startswith("edit_emoji:"))
async def handle_edit_emoji_click(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    emoji_key = callback.data.split(":")[1]
    label = EMOJI_LABELS.get(emoji_key, emoji_key)

    await state.update_data(target_emoji_key=emoji_key)
    await state.set_state(AdminEmojiState.waiting_for_emoji)

    text = (
        f"{emoji_manager.get('welcome')} <b>{label}</b> uchun yangi Premium Emojini tanlang!\n\n"
        f"<b>Qanday o'zgartiriladi:</b>\n"
        f"1. Telegram Premium emojilar ro'yxatidan istalgan birini tanlab shu chatga yuboring!\n"
        f"2. Yoki uning Custom Emoji ID raqamini matn qilib yuboring.\n\n"
        f"<i>(Bekor qilish uchun /cancel deb yozing)</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

@router.message(AdminEmojiState.waiting_for_emoji)
async def process_emoji_input(message: Message, state: FSMContext, redis: Redis):
    if not is_admin(message.from_user.id):
        return

    if message.text == "/cancel":
        await state.clear()
        await message.answer("Emoji o'zgartirish bekor qilindi.", parse_mode="HTML")
        return

    data = await state.get_data()
    emoji_key = data.get("target_emoji_key")

    emoji_id = None

    if message.entities:
        for ent in message.entities:
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                emoji_id = str(ent.custom_emoji_id)
                break

    if not emoji_id and message.text and message.text.strip().isdigit():
        emoji_id = message.text.strip()

    if not emoji_id:
        await message.answer(f"{emoji_manager.get('warning')} Iltimos, Telegram Premium emoji yuboring yoki custom_emoji_id raqamini kiriting:", parse_mode="HTML")
        return

    await emoji_manager.set_custom_emoji(emoji_key, emoji_id, redis)
    await state.clear()

    preview = emoji_manager.get(emoji_key)
    label = EMOJI_LABELS.get(emoji_key, emoji_key)

    await message.answer(
        f"{emoji_manager.get('success')} <b>{label}</b> emojisi muvaffaqiyatli yangilandi!\n\n"
        f"Ko'rinishi: {preview}\n"
        f"Emoji ID: <code>{emoji_id}</code>",
        reply_markup=get_admin_dashboard_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminProjectIDState.waiting_for_project_id, F.text)
async def process_admin_change_project_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    new_id = message.text.strip()
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Bekor qilindi.", parse_mode="HTML")
        return

    settings.OPENBUDGET_PROJECT_ID = new_id
    await state.clear()
    await message.answer(
        f"{emoji_manager.get('success')} OpenBudget Loyiha ID saqlandi: <code>{settings.OPENBUDGET_PROJECT_ID}</code>",
        reply_markup=get_admin_dashboard_keyboard(),
        parse_mode="HTML"
    )

@router.message(AdminBroadcastState.waiting_for_ad_text, F.text)
async def process_admin_group_broadcast(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    ad_text = message.text
    if ad_text == "/cancel":
        await state.clear()
        await message.answer("Reklama bekor qilindi.", parse_mode="HTML")
        return

    if ad_text.strip() == "1":
        ad_text = (
            f"{emoji_manager.get('welcome')} <b>OPENBUDGET RASMIY BOTI</b>\n\n"
            f"O'z raqamingiz va yaqinlaringiz raqamidan ovoz bering hamda pul mukofotini oling!\n\n"
            f"🔗 <b>Referal tizimi:</b> Do'stlaringizni taklif qiling va ular bergan har bir ovoz uchun doimiy daromad ishlang!\n\n"
            f"{emoji_manager.get('finger_down')} <b>Pastdagi tugmani bosib hoziroq boshlang:</b>"
        )

    stmt = select(Group).where(Group.is_active == True)
    res = await session.execute(stmt)
    groups = res.scalars().all()

    if not groups:
        await message.answer(f"{emoji_manager.get('warning')} Faol guruhlar topilmadi.", parse_mode="HTML")
        await state.clear()
        return

    bot_info = await message.bot.get_me()
    keyboard = get_group_promo_keyboard(bot_info.username)

    sent_count = 0
    fail_count = 0

    await message.answer(f"{emoji_manager.get('speaker')} {len(groups)} ta guruhga reklama yuborish boshlandi...", parse_mode="HTML")

    for g in groups:
        try:
            await message.bot.send_message(
                chat_id=g.chat_id,
                text=ad_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to broadcast to group {g.chat_id}: {e}")
            fail_count += 1

    await state.clear()
    await message.answer(
        f"{emoji_manager.get('success')} REKLAMA TUGATILDI!\n{emoji_manager.get('success')} Yuborildi: {sent_count} ta\n{emoji_manager.get('danger')} Xato: {fail_count} ta",
        reply_markup=get_admin_dashboard_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("admin_pay:"))
async def handle_admin_pay_ticket(callback: CallbackQuery, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⚠️ Admin emassiz!", show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[1])
    success, msg = await process_ticket_action(
        session=session,
        ticket_id=ticket_id,
        action="pay",
        admin_telegram_id=callback.from_user.id,
        bot=callback.bot
    )

    if success:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{emoji_manager.get('success')} <b>TO'LANDI!</b>",
            parse_mode="HTML"
        )
        await callback.answer("✅ To'landi!", show_alert=True)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)

@router.callback_query(F.data.startswith("admin_reject:"))
async def handle_admin_reject_click(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⚠️ Admin emassiz!", show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        f"{callback.message.text}\n\n{emoji_manager.get('danger')} <b>Rad etish sababi:</b>",
        reply_markup=get_admin_reject_reasons_keyboard(ticket_id),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reject_preset:"))
async def handle_admin_reject_preset(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⚠️ Admin emassiz!", show_alert=True)
        return

    parts = callback.data.split(":")
    ticket_id = int(parts[1])
    reason_code = parts[2]

    if reason_code == "card_invalid":
        reason = "Karta yoki telefon raqami noto'g'ri kiritilgan."
    elif reason_code == "unverified":
        reason = "Ovoz berish rasman tasdiqlanmagan."
    elif reason_code == "duplicate":
        reason = "Ushbu raqam bo'yicha ilgari zayavka to'lab berilgan."
    elif reason_code == "custom":
        await state.update_data(reject_target_ticket_id=ticket_id)
        await state.set_state(AdminRejectState.waiting_for_reason)
        await callback.message.answer(f"{emoji_manager.get('danger')} Rad etish sababini yozing (/cancel - bekor qilish):", parse_mode="HTML")
        await callback.answer()
        return

    success, msg = await process_ticket_action(
        session=session,
        ticket_id=ticket_id,
        action="reject",
        admin_telegram_id=callback.from_user.id,
        reason=reason,
        bot=callback.bot
    )

    if success:
        safe_r = html.escape(reason)
        await callback.message.edit_text(
            f"{callback.message.text}\n\n{emoji_manager.get('danger')} <b>RAD ETILDI.</b> Sababi: <code>{safe_r}</code>",
            parse_mode="HTML"
        )
        await callback.answer("🔴 Rad etildi!", show_alert=True)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)

@router.message(AdminRejectState.waiting_for_reason, F.text)
async def process_admin_custom_reject_reason(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    reason_text = message.text.strip()
    if reason_text == "/cancel":
        await state.clear()
        await message.answer("Rad etish bekor qilindi.", parse_mode="HTML")
        return

    data = await state.get_data()
    ticket_id = data.get("reject_target_ticket_id")

    success, msg = await process_ticket_action(
        session=session,
        ticket_id=ticket_id,
        action="reject",
        admin_telegram_id=message.from_user.id,
        reason=reason_text,
        bot=message.bot
    )

    await state.clear()

    if success:
        safe_r = html.escape(reason_text)
        await message.answer(
            f"{emoji_manager.get('danger')} <b>ZAYAVKA #{ticket_id} rad etildi.</b>\nSabab: <code>{safe_r}</code>",
            parse_mode="HTML"
        )
    else:
        await message.answer(f"{emoji_manager.get('danger')} Xatolik: {msg}", parse_mode="HTML")
