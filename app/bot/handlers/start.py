import logging
import html
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, CommandObject
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from app.database.models import User, Vote, VoteStatus
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.bot.keyboards.inline import get_admin_dashboard_keyboard, get_payout_proof_channel_keyboard, get_help_contacts_keyboard, PAYOUT_CHANNEL_DIRECT_URL
from app.services.countdown import get_countdown_info
from app.services.emoji_manager import emoji_manager
from app.config import settings

logger = logging.getLogger(__name__)
router = Router()

def check_is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list

@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, command: CommandObject, session: AsyncSession):
    user_id = message.from_user.id
    full_name = message.from_user.full_name or "Foydalanuvchi"
    username = message.from_user.username

    referrer_id = None
    if command.args and command.args.startswith("ref_"):
        try:
            potential_ref = int(command.args.split("ref_")[1])
            if potential_ref != user_id:
                ref_stmt = select(User).where(User.telegram_id == potential_ref)
                ref_res = await session.execute(ref_stmt)
                if ref_res.scalar_one_or_none():
                    referrer_id = potential_ref
        except Exception as e:
            logger.warning(f"Referral parsing error: {e}")

    try:
        stmt = select(User).where(User.telegram_id == user_id)
        res = await session.execute(stmt)
        db_user = res.scalar_one_or_none()

        if not db_user:
            db_user = User(
                telegram_id=user_id,
                full_name=full_name,
                username=username,
                referrer_id=referrer_id
            )
            session.add(db_user)
            await session.commit()
        else:
            if db_user.full_name != full_name:
                db_user.full_name = full_name
            if username and db_user.username != username:
                db_user.username = username
            await session.commit()

        is_adm = check_is_admin(user_id)
        c_str, c_html, is_started = get_countdown_info()

        if not is_adm:
            welcome_text = (
                f"{emoji_manager.get('welcome')} <b>Assalomu alaykum, {html.escape(full_name)}!</b>\n\n"
                f"<blockquote>{emoji_manager.get('building')} <b>OpenBudget rasmiy botiga xush kelibsiz.</b>\n\n"
                f"{emoji_manager.get('timer')} <b>Ovoz berish boshlanishiga:</b> <code>{c_str}</code> qoldi!\n\n"
                f"{emoji_manager.get('speaker')} OpenBudget mavsumi boshlanishi bilan botimiz to'liq ishga tushadi.</blockquote>\n\n"
                f"{emoji_manager.get('finger_down')} Kerakli bo'limni tanlang:"
            )
        else:
            welcome_text = (
                f"{emoji_manager.get('admin')} <b>Assalomu alaykum, {html.escape(full_name)} (ADMIN)!</b>\n\n"
                f"<blockquote>{emoji_manager.get('admin')} Admin paneldan bemalol foydalanishingiz mumkin.</blockquote>\n\n"
                f"{emoji_manager.get('finger_down')} Menyudan bo'limni tanlang:"
            )

        await message.answer(
            welcome_text,
            reply_markup=get_main_menu_keyboard(is_admin=is_adm),
            parse_mode="HTML"
        )
    except Exception as ex:
        logger.error(f"Error handling cmd_start for user {user_id}: {ex}", exc_info=True)
        is_adm = check_is_admin(user_id)
        fallback_text = (
            f"{emoji_manager.get('welcome')} <b>Assalomu alaykum, {html.escape(full_name)}!</b>\n\n"
            f"<blockquote>{emoji_manager.get('building')} OpenBudget rasmiy botiga xush kelibsiz.</blockquote>\n\n"
            f"{emoji_manager.get('finger_down')} Kerakli bo'limni tanlang:"
        )
        await message.answer(fallback_text, reply_markup=get_main_menu_keyboard(is_admin=is_adm), parse_mode="HTML")

@router.message(F.text.contains("Statistika"), F.chat.type == "private")
async def show_public_general_stats(message: Message, session: AsyncSession):
    stmt = (
        select(
            User.full_name,
            User.manual_votes_offset,
            func.count(Vote.id).label("real_vote_count")
        )
        .outerjoin(Vote, (Vote.telegram_id == User.telegram_id) & (Vote.status == VoteStatus.VERIFIED))
        .group_by(User.telegram_id, User.full_name, User.manual_votes_offset)
    )
    res = await session.execute(stmt)
    rows = res.all()

    # Calculate user displayed votes including manual offsets
    user_stats_list = []
    for r in rows:
        disp_votes = max(0, r.real_vote_count + (r.manual_votes_offset or 0))
        user_stats_list.append((r.full_name, disp_votes))

    # Sort descending by displayed vote count
    user_stats_list.sort(key=lambda x: x[1], reverse=True)

    if not user_stats_list:
        stats_text = f"{emoji_manager.get('votes_icon')} <b>UMUMIY OVOZLAR STATISTIKASI:</b>\n\n<i>Hali ovozlar berilmagan.</i>"
    else:
        stats_text = f"{emoji_manager.get('votes_icon')} <b>UMUMIY OVOZLAR STATISTIKASI:</b>\n\n"
        for idx, (name_raw, count) in enumerate(user_stats_list, 1):
            name = html.escape(name_raw or f"Foydalanuvchi_{idx}")
            stats_text += f"{idx}. <b>{name}</b> — <b>{count} ta</b> ovoz\n"

    await message.answer(stats_text, parse_mode="HTML")

@router.message(F.text.contains("Yordam"), F.chat.type == "private")
async def show_help_rules(message: Message):
    c_str, c_html, is_started = get_countdown_info()
    n1 = emoji_manager.get('num_1')
    n2 = emoji_manager.get('num_2')
    n3 = emoji_manager.get('num_3')
    spk = emoji_manager.get('speaker')
    help_ico = emoji_manager.get('help')
    tmr = emoji_manager.get('timer')

    text = (
        f"{help_ico} <b>BOT QOIDALARI & YORDAM:</b>\n\n"
        f"<blockquote>{tmr} <b>Boshlanishiga:</b> <code>{c_str}</code> qoldi!\n\n"
        f"{n1} OpenBudget boshlangach 'Ovoz berish' tugmasini bosing.\n"
        f"{n2} SMS kodni kiriting va ovozingizni tasdiqlang.\n"
        f"{n3} Pul mukofotini kartangizga yechib oling.</blockquote>\n\n"
        f"{spk} Barcha to'lovlar rasmiy kanalimizda e'lon qilinadi.\n\n"
        f"<blockquote>💬 <b>Savollar bo'yicha murojaat uchun pastdagi tugmalardan foydalaning!</b></blockquote>"
    )
    await message.answer(text, reply_markup=get_help_contacts_keyboard(), parse_mode="HTML")

@router.message(F.text.contains("To'lovlar kanali"), F.chat.type == "private")
async def show_payout_channel(message: Message):
    text = (
        f"{emoji_manager.get('channel')} <a href=\"{PAYOUT_CHANNEL_DIRECT_URL}\"><b>RASMIY TO'LOVLAR KANALIMIZ</b></a>\n\n"
        f"<blockquote>Barcha to'lovlar va cheklar shaffof tarzda kanalimizga joylab boriladi.\n\n"
        f"Kanalga o'tish uchun pastdagi tugmani bosing!</blockquote>"
    )
    await message.answer(text, reply_markup=get_payout_proof_channel_keyboard(), parse_mode="HTML")

@router.message(F.text.contains("Mening havolam"), F.chat.type == "private")
async def show_referral_link(message: Message, session: AsyncSession):
    user_id = message.from_user.id
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    stmt = select(User).where(User.telegram_id == user_id)
    res = await session.execute(stmt)
    u = res.scalar_one_or_none()
    count = u.referral_count if u else 0

    c_str, c_html, is_started = get_countdown_info()

    text = (
        f"{emoji_manager.get('link')} <b>SHAXSIY TAKLIF HAVOLANGIZ:</b>\n\n"
        f"<blockquote><code>{ref_link}</code>\n\n"
        f"{emoji_manager.get('users_icon')} Tasdiqlangan takliflaringiz: <b>{count} ta</b>\n"
        f"<i>(Eslatma: Taklifingiz tasdiqlanishi va bonus berilishi uchun siz chaqirgan do'stingiz kamida 1 ta ovoz berishi kerak)</i></blockquote>"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(F.text.contains("Top Referrallar"), F.chat.type == "private")
async def show_top_referrals(message: Message, session: AsyncSession):
    stmt = select(User).order_by(desc(User.referral_count)).limit(10)
    res = await session.execute(stmt)
    top_users = res.scalars().all()

    if not top_users or all(u.referral_count == 0 for u in top_users):
        await message.answer(
            f"{emoji_manager.get('top_ref')} <b>TOP REFERRALLAR:</b>\n\n<blockquote>Hozircha tasdiqlangan referrallar yo'q.</blockquote>",
            parse_mode="HTML"
        )
        return

    text = f"{emoji_manager.get('top_ref')} <b>TOP REFERRALLAR REYTINGI:</b>\n\n<blockquote>"
    medals = ["🥇", "🥈", "🥉"]
    for idx, u in enumerate(top_users, 1):
        name = html.escape(u.full_name or f"Foydalanuvchi_{idx}")
        medal = medals[idx - 1] if idx <= 3 else f"{idx}."
        text += f"{medal} <b>{name}</b> — {u.referral_count} ta tasdiqlangan taklif\n"
    text += "</blockquote>"

    await message.answer(text, parse_mode="HTML")
