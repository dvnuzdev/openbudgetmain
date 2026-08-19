import logging
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.asyncio import Redis

from app.database.models import Vote, VoteStatus, User
from app.bot.states import VoteStates
from app.bot.keyboards.reply import get_phone_request_keyboard, get_main_menu_keyboard, get_cancel_keyboard
from app.bot.keyboards.inline import get_payout_choice_keyboard
from app.services.openbudget_api import openbudget_api
from app.services.anti_fraud import clean_phone_number, is_valid_uzbek_phone
from app.services.emoji_manager import emoji_manager
from app.config import settings

logger = logging.getLogger(__name__)
router = Router()

def check_is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list

@router.message(F.text.contains("Bekor qilish"))
async def cancel_handler(message: Message, state: FSMContext):
    is_adm = check_is_admin(message.from_user.id)
    await state.clear()
    cancel_text = f"{emoji_manager.get('cancel')} <b>Jarayon bekor qilindi.</b>\n\n{emoji_manager.get('finger_down')} Menyudan bo'limni tanlang:"
    await message.answer(cancel_text, reply_markup=get_main_menu_keyboard(is_admin=is_adm), parse_mode="HTML")

@router.message(F.text.contains("Ovoz berish"), F.chat.type == "private")
async def start_self_vote_process(message: Message, state: FSMContext, session: AsyncSession, redis: Redis):
    user_id = message.from_user.id

    if not check_is_admin(user_id):
        from app.services.countdown import get_countdown_info
        c_str, c_html, is_started = get_countdown_info()
        await message.answer(
            c_html,
            parse_mode="HTML"
        )
        return

    await state.clear()
    await state.set_state(VoteStates.waiting_for_phone)

    text = (
        f"{emoji_manager.get('vote')} <b>OpenBudget loyihasiga o'z raqamingizdan ovoz berish:</b>\n\n"
        "Ovoz berish uchun pastdagi <b>📱 Telefon raqamni yuborish</b> tugmasini bosing:"
    )
    await message.answer(text, reply_markup=get_phone_request_keyboard(), parse_mode="HTML")

@router.message(F.text.contains("Boshqa raqamdan ovoz"), F.chat.type == "private")
async def start_other_phone_vote(message: Message, state: FSMContext, redis: Redis):
    user_id = message.from_user.id

    if not check_is_admin(user_id):
        from app.services.countdown import get_countdown_info
        c_str, c_html, is_started = get_countdown_info()
        await message.answer(
            c_html,
            parse_mode="HTML"
        )
        return

    await state.clear()
    await state.set_state(VoteStates.waiting_for_phone)

    text = (
        f"{emoji_manager.get('other_phone')} <b>Boshqa telefon raqami orqali ovoz berish:</b>\n\n"
        "Iltimos, ovoz beriladigan telefon raqamini quyidagi formatda yozib yuboring:\n"
        "Masalan: <code>+998901234567</code> yoki <code>901234567</code>"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")

@router.message(VoteStates.waiting_for_phone, F.contact)
async def process_phone_contact(message: Message, state: FSMContext, session: AsyncSession):
    contact = message.contact
    raw_phone = contact.phone_number
    await handle_phone_submission(message, state, session, raw_phone)

@router.message(VoteStates.waiting_for_phone, F.text)
async def process_phone_text(message: Message, state: FSMContext, session: AsyncSession):
    raw_phone = message.text.strip()
    await handle_phone_submission(message, state, session, raw_phone)

async def handle_phone_submission(message: Message, state: FSMContext, session: AsyncSession, raw_phone: str):
    normalized_phone = clean_phone_number(raw_phone)
    is_adm = check_is_admin(message.from_user.id)

    if not is_valid_uzbek_phone(normalized_phone):
        await message.answer(
            f"{emoji_manager.get('warning')} Faqat O'zbekiston mobil raqamlari (+998) orqali ovoz berish mumkin.",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    stmt = select(Vote).where(Vote.voted_phone_number == normalized_phone)
    res = await session.execute(stmt)
    existing_vote = res.scalar_one_or_none()

    if existing_vote and existing_vote.status == VoteStatus.VERIFIED:
        await message.answer(
            f"{emoji_manager.get('danger')} <b>Ushbu raqam ({normalized_phone}) orqali allaqachon ovoz berilgan!</b>\n"
            f"Bitta raqamdan faqat 1 marta ovoz berish mumkin.",
            reply_markup=get_main_menu_keyboard(is_admin=is_adm),
            parse_mode="HTML"
        )
        await state.clear()
        return

    await message.answer(
        f"{emoji_manager.get('timer')} OpenBudget serveridan SMS kod so'ralmoqda...",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    
    success, api_msg, extra = await openbudget_api.send_otp(normalized_phone)

    if not success:
        await message.answer(
            f"{emoji_manager.get('danger')} {api_msg}",
            reply_markup=get_main_menu_keyboard(is_admin=is_adm),
            parse_mode="HTML"
        )
        await state.clear()
        return

    await state.update_data(voted_phone=normalized_phone)
    await state.set_state(VoteStates.waiting_for_otp)

    await message.answer(
        f"{emoji_manager.get('speaker')} <b>SMS tasdiqlash kodi +{normalized_phone} raqamiga yuborildi!</b>\n\n"
        f"Telefonga kelgan 6 xonali SMS kodni botga kiriting:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

@router.message(VoteStates.waiting_for_otp)
async def process_otp_code(message: Message, state: FSMContext, session: AsyncSession, bot_identifier: str = "bot1"):
    otp_code = message.text.strip() if message.text else ""
    data = await state.get_data()
    voted_phone = data.get("voted_phone")
    user_id = message.from_user.id
    is_adm = check_is_admin(user_id)

    if not otp_code.isdigit() or len(otp_code) != 6:
        await message.answer(
            f"{emoji_manager.get('warning')} Iltimos, faqat 6 xonali raqamdan iborat SMS kodni kiriting.",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    await message.answer(
        f"{emoji_manager.get('timer')} SMS kod OpenBudget bazasida tekshirilmoqda...",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )

    verified, msg, openbudget_tx_id = await openbudget_api.verify_otp(voted_phone, otp_code)

    if not verified:
        await message.answer(
            f"{emoji_manager.get('danger')} {msg}\n\nQayta urinib ko'ring yoki kodni tekshiring.",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    user_stmt = select(User).where(User.telegram_id == user_id)
    u_res = await session.execute(user_stmt)
    user_obj = u_res.scalar_one_or_none()

    vote_stmt = select(Vote).where(Vote.voted_phone_number == voted_phone)
    v_res = await session.execute(vote_stmt)
    vote_rec = v_res.scalar_one_or_none()

    if not vote_rec:
        vote_rec = Vote(
            telegram_id=user_id,
            voted_phone_number=voted_phone,
            openbudget_project_id=settings.OPENBUDGET_PROJECT_ID,
            openbudget_tx_id=openbudget_tx_id,
            bot_identifier=bot_identifier,
            status=VoteStatus.VERIFIED
        )
        session.add(vote_rec)
    else:
        vote_rec.status = VoteStatus.VERIFIED
        vote_rec.openbudget_tx_id = openbudget_tx_id
        vote_rec.bot_identifier = bot_identifier

    if user_obj:
        user_obj.balance_uzs += settings.DEFAULT_REWARD_PER_VOTE

        if user_obj.referrer_id:
            referrer_stmt = select(User).where(User.telegram_id == user_obj.referrer_id)
            ref_res = await session.execute(referrer_stmt)
            referrer_obj = ref_res.scalar_one_or_none()

            if referrer_obj:
                referrer_obj.referral_count += 1
                try:
                    await message.bot.send_message(
                        chat_id=referrer_obj.telegram_id,
                        text=(
                            f"{emoji_manager.get('welcome')} <b>YANGI REFERAL!</b>\n\n"
                            f"Siz taklif qilgan do'stingiz OpenBudget loyihasiga ovoz berdi!\n"
                            f"{emoji_manager.get('users_icon')} Jami taklif qilgan do'stlaringiz: <b>{referrer_obj.referral_count} ta</b>"
                        ),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify referrer {referrer_obj.telegram_id}: {e}")

    await session.commit()
    await session.refresh(vote_rec)

    await state.clear()

    cur_bal = user_obj.balance_uzs if user_obj else settings.DEFAULT_REWARD_PER_VOTE

    congratulations_text = (
        f"{emoji_manager.get('success')} <b>TABRIKLAYMIZ! OVOZINGIZ ({voted_phone}) MUVAFFAQIYATLI TASDIQLANDI!</b>\n\n"
        f"{emoji_manager.get('paid_icon')} OpenBudget tranzaksiya ID: <code>{openbudget_tx_id}</code>\n"
        f"{emoji_manager.get('balance')} Balansingizga: <b>+{settings.DEFAULT_REWARD_PER_VOTE:,} UZS</b> qo'shildi!\n"
        f"{emoji_manager.get('lock_icon')} <b>Jami balansingiz: {cur_bal:,} UZS</b>\n\n"
        f"{emoji_manager.get('finger_down')} Mukofot pulini hoziroq kartangizga yechib olishingiz yoki boshqa raqamdan ovoz berishingiz mumkin:"
    )

    await message.answer(
        congratulations_text,
        reply_markup=get_payout_choice_keyboard(vote_id=vote_rec.id),
        parse_mode="HTML"
    )
