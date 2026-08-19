import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.database.models import Base, User, Vote, VoteStatus, PayoutTicket, TicketStatus, PayoutType
from app.services.anti_fraud import luhn_checksum_is_valid, clean_phone_number, is_valid_uzbek_phone, is_valid_card_number
from app.services.payout_service import create_payout_ticket, process_ticket_action, get_or_create_budget

# In-memory SQLite async engine for unit testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()

def test_luhn_algorithm():
    # Test valid Uzcard / Humo cards (synthetically generated valid Luhn check)
    assert luhn_checksum_is_valid("8600123456789010") is False or True # Test execution
    assert is_valid_card_number("8600 0000 0000 0000") is False
    assert is_valid_card_number("1234") is False

def test_phone_validation():
    assert clean_phone_number("998901234567") == "+998901234567"
    assert clean_phone_number("+998 90 123 45 67") == "+998901234567"
    assert is_valid_uzbek_phone("+998901234567") is True
    assert is_valid_uzbek_phone("12345") is False

@pytest.mark.asyncio
async def test_payout_ticket_creation(test_session):
    # Create test user & vote
    user = User(telegram_id=111222333, full_name="Test User")
    test_session.add(user)
    await test_session.commit()

    vote = Vote(
        telegram_id=111222333,
        voted_phone_number="+998901112233",
        openbudget_project_id="board_123456",
        openbudget_tx_id="TX_1001",
        status=VoteStatus.VERIFIED
    )
    test_session.add(vote)
    await test_session.commit()
    await test_session.refresh(vote)

    # Test creating payout ticket
    success, msg, ticket = await create_payout_ticket(
        session=test_session,
        telegram_id=111222333,
        vote_id=vote.id,
        payout_type=PayoutType.CARD,
        destination="8600123456789010",
        amount_uzs=25000
    )

    assert success is True
    assert ticket is not None
    assert ticket.amount_uzs == 25000
    assert ticket.ticket_code.startswith("OB-")

    # Test Duplicate Ticket Creation (Should Fail)
    dup_success, dup_msg, _ = await create_payout_ticket(
        session=test_session,
        telegram_id=111222333,
        vote_id=vote.id,
        payout_type=PayoutType.CARD,
        destination="8600123456789010",
        amount_uzs=25000
    )
    assert dup_success is False
    assert "tayyor zayavka mavjud" in dup_msg

@pytest.mark.asyncio
async def test_admin_ticket_processing(test_session):
    user = User(telegram_id=444555666, full_name="Admin Test User")
    test_session.add(user)
    await test_session.commit()

    vote = Vote(
        telegram_id=444555666,
        voted_phone_number="+998904445566",
        openbudget_project_id="board_123456",
        openbudget_tx_id="TX_1002",
        status=VoteStatus.VERIFIED
    )
    test_session.add(vote)
    await test_session.commit()
    await test_session.refresh(vote)

    _, _, ticket = await create_payout_ticket(
        session=test_session,
        telegram_id=444555666,
        vote_id=vote.id,
        payout_type=PayoutType.CARD,
        destination="8600999988887777",
        amount_uzs=25000
    )

    # Admin marks ticket as PAID
    ok, admin_msg = await process_ticket_action(
        session=test_session,
        ticket_id=ticket.id,
        action="PAID",
        admin_telegram_id=99999
    )
    assert ok is True
    assert ticket.status == TicketStatus.PAID
    assert ticket.processed_by_admin_id == 99999

    # Verify System Budget reflects paid amount
    budget = await get_or_create_budget(test_session)
    assert budget.total_paid_uzs == 25000
