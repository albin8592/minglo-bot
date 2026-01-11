import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_USERNAME = "minglochat_bot"
PREMIUM_REFERRALS = 100

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db: asyncpg.Pool = None

# ================= STATES =================
IDLE = "IDLE"
WAITING = "WAITING"
CHATTING = "CHATTING"

user_state = {}
user_mode = {}        # RANDOM / GIRLS
waiting_random = set()
waiting_girls = set()
active_chats = {}

# ================= BUTTONS =================
BTN_RANDOM = "🔀 Random Chat (Free)"
BTN_GIRLS = "👧 Find Girls (Premium)"
BTN_NEXT = "⏭ Next"
BTN_STOP = "❌ Stop"
BTN_INVITE = "📢 Invite & Earn Premium"
BTN_VIP = "💎 VIP Status"

# ================= KEYBOARD =================
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_RANDOM)],
            [KeyboardButton(text=BTN_GIRLS)],
            [KeyboardButton(text=BTN_NEXT), KeyboardButton(text=BTN_STOP)],
            [KeyboardButton(text=BTN_INVITE), KeyboardButton(text=BTN_VIP)],
        ],
        resize_keyboard=True
    )

# ================= DATABASE =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        gender TEXT,
        referrals INT DEFAULT 0,
        premium BOOLEAN DEFAULT FALSE
    )
    """)

async def get_user(uid):
    return await db.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid):
    await db.execute(
        "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING",
        uid
    )

# ================= START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    args = message.text.split()

    await create_user(uid)
    user_state[uid] = IDLE

    # Referral system
    if len(args) == 2 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            await db.execute(
                "UPDATE users SET referrals = referrals + 1 WHERE user_id=$1",
                ref
            )
            ref_user = await get_user(ref)
            if ref_user and ref_user["referrals"] >= PREMIUM_REFERRALS and not ref_user["premium"]:
                await db.execute(
                    "UPDATE users SET premium=TRUE WHERE user_id=$1",
                    ref
                )
                await bot.send_message(ref, "🎉 You are now *PREMIUM* 💎", parse_mode="Markdown")

    await message.answer(
        "💜 *Welcome to Minglo Chat*\n\nChoose an option 👇",
        reply_markup=main_kb(),
        parse_mode="Markdown"
    )

# ================= MATCH ENGINE =================
async def start_search(uid, message, mode):
    if user_state.get(uid) == CHATTING:
        await message.answer("❌ Already chatting\nUse ⏭ Next or ❌ Stop")
        return

    user_state[uid] = WAITING
    user_mode[uid] = mode

    pool = waiting_random if mode == "RANDOM" else waiting_girls

    for other in list(pool):
        if other != uid:
            pool.remove(other)

            active_chats[uid] = other
            active_chats[other] = uid
            user_state[uid] = CHATTING
            user_state[other] = CHATTING

            await bot.send_message(uid, "🎉 Match found!", reply_markup=main_kb())
            await bot.send_message(other, "🎉 Match found!", reply_markup=main_kb())
            return

    pool.add(uid)
    await message.answer("⏳ Waiting for a partner...")

# ================= BUTTON HANDLERS =================
@dp.message(lambda m: m.text == BTN_RANDOM)
async def random_chat(message: types.Message):
    await start_search(message.from_user.id, message, "RANDOM")

@dp.message(lambda m: m.text == BTN_GIRLS)
async def find_girls(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)

    if not user or not user["premium"]:
        await message.answer("💎 Premium required\nInvite 100 users to unlock")
        return

    await start_search(uid, message, "GIRLS")

@dp.message(lambda m: m.text == BTN_NEXT)
async def next_chat(message: types.Message):
    uid = message.from_user.id

    if user_state.get(uid) != CHATTING:
        await message.answer("⚠️ You are not chatting")
        return

    other = active_chats.pop(uid)
    active_chats.pop(other, None)

    user_state[uid] = IDLE
    user_state[other] = IDLE

    await bot.send_message(other, "⏭ Partner moved to next chat")

    await start_search(uid, message, user_mode.get(uid, "RANDOM"))

@dp.message(lambda m: m.text == BTN_STOP)
async def stop_chat(message: types.Message):
    uid = message.from_user.id

    if user_state.get(uid) == CHATTING:
        other = active_chats.pop(uid)
        active_chats.pop(other, None)
        user_state[other] = IDLE
        await bot.send_message(other, "❌ Partner stopped chat")

    waiting_random.discard(uid)
    waiting_girls.discard(uid)
    user_state[uid] = IDLE

    await message.answer("🛑 Chat stopped", reply_markup=main_kb())

@dp.message(lambda m: m.text == BTN_INVITE)
async def invite(message: types.Message):
    uid = message.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    await message.answer(
        f"🎁 *Invite & Earn Premium*\n\n"
        f"🔗 {link}\n\n"
        f"👥 Invite 100 users → AUTO PREMIUM 💎",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == BTN_VIP)
async def vip_status(message: types.Message):
    user = await get_user(message.from_user.id)
    status = "💎 PREMIUM" if user["premium"] else "🆓 FREE"
    await message.answer(
        f"💎 *VIP STATUS*\n\n"
        f"Status: {status}\n"
        f"Referrals: {user['referrals']} / 100",
        parse_mode="Markdown"
    )

# ================= MESSAGE RELAY =================
@dp.message(lambda m: m.from_user.id in active_chats)
async def relay(message: types.Message):
    await bot.send_message(active_chats[message.from_user.id], message.text)

# ================= RUN =================
async def main():
    await init_db()
    print("🔥 Minglo Bot Running – PROFESSIONAL BUILD")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
