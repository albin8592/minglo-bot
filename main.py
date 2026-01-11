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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db: asyncpg.Pool = None

PREMIUM_REFERRALS = 100

# ================= BUTTONS =================
BTN_RANDOM = "🔀 Random Chat (Free)"
BTN_GIRLS = "👧 Find Girls"
BTN_BOYS = "👦 Find Boys"
BTN_INVITE = "📢 Invite & Earn Premium"
BTN_VIP = "💎 VIP Status"
BTN_NEXT = "⏭ Next"
BTN_STOP = "❌ Stop"
BTN_REPORT = "🚫 Block & Report"

# ================= MEMORY =================
waiting = set()
active = {}

# ================= DB =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        name TEXT,
        age INT,
        place TEXT,
        gender TEXT,
        referrals INT DEFAULT 0,
        premium BOOLEAN DEFAULT FALSE
    );
    """)

async def get_user(uid):
    return await db.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid):
    await db.execute(
        "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING",
        uid
    )

# ================= KEYBOARD =================
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_RANDOM)],
            [KeyboardButton(text=BTN_GIRLS), KeyboardButton(text=BTN_BOYS)],
            [KeyboardButton(text=BTN_NEXT), KeyboardButton(text=BTN_STOP)],
            [KeyboardButton(text=BTN_INVITE)],
            [KeyboardButton(text=BTN_VIP), KeyboardButton(text=BTN_REPORT)],
        ],
        resize_keyboard=True
    )

def gender_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]],
        resize_keyboard=True
    )

def mask(name):
    return name[0] + "***"

# ================= START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    args = message.text.split()

    await create_user(uid)

    # referral
    if len(args) == 2 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            ref_user = await get_user(ref)
            if ref_user:
                await db.execute(
                    "UPDATE users SET referrals=referrals+1 WHERE user_id=$1",
                    ref
                )
                ref_user = await get_user(ref)
                if ref_user["referrals"] >= PREMIUM_REFERRALS and not ref_user["premium"]:
                    await db.execute(
                        "UPDATE users SET premium=TRUE WHERE user_id=$1",
                        ref
                    )
                    await bot.send_message(ref, "🎉 Congrats! You are now *PREMIUM* 💎", parse_mode="Markdown")

    user = await get_user(uid)

    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome to *Minglo Chat*", reply_markup=main_kb(), parse_mode="Markdown")

# ================= PROFILE =================
@dp.message()
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user:
        return

    if user["gender"]:
        return

    if user["name"] is None:
        await db.execute("UPDATE users SET name=$1 WHERE user_id=$2", message.text, uid)
        await message.answer("🎂 Age?")
        return

    if user["age"] is None:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await db.execute("UPDATE users SET age=$1 WHERE user_id=$2", int(message.text), uid)
        await message.answer("📍 Place?")
        return

    if user["place"] is None:
        await db.execute("UPDATE users SET place=$1 WHERE user_id=$2", message.text, uid)
        await message.answer("👤 Select Gender", reply_markup=gender_kb())
        return

    if user["gender"] is None and message.text in ["👦 Boy", "👧 Girl"]:
        await db.execute("UPDATE users SET gender=$1 WHERE user_id=$2", message.text, uid)
        await message.answer("✅ Profile completed!", reply_markup=main_kb())
        return

# ================= MATCH =================
async def try_match(uid, message):
    if uid in active:
        await message.answer("❌ You are already chatting.\nUse ⏭ Next or ❌ Stop")
        return

    for other in list(waiting):
        if other != uid and other not in active:
            waiting.remove(other)
            active[uid] = other
            active[other] = uid

            me = await get_user(uid)
            him = await get_user(other)

            await bot.send_message(
                uid,
                f"🎉 Match Found!\n👤 {mask(him['name'])}, {him['age']}, {him['place']}",
                reply_markup=main_kb()
            )
            await bot.send_message(
                other,
                f"🎉 Match Found!\n👤 {mask(me['name'])}, {me['age']}, {me['place']}",
                reply_markup=main_kb()
            )
            return

    waiting.add(uid)
    await message.answer("⏳ Waiting for a partner...")

# ================= BUTTON HANDLERS =================
@dp.message(lambda m: m.text == BTN_RANDOM)
async def random_chat(message: types.Message):
    await try_match(message.from_user.id, message)

@dp.message(lambda m: m.text == BTN_NEXT)
async def next_chat(message: types.Message):
    uid = message.from_user.id
    if uid in active:
        other = active.pop(uid)
        active.pop(other, None)
        await bot.send_message(other, "⏭ Partner moved to next chat")
    await try_match(uid, message)

@dp.message(lambda m: m.text == BTN_STOP)
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    if uid in active:
        other = active.pop(uid)
        active.pop(other, None)
        await bot.send_message(other, "❌ Partner stopped chat")
    waiting.discard(uid)
    await message.answer("🛑 Chat stopped", reply_markup=main_kb())

@dp.message(lambda m: m.text == BTN_INVITE)
async def invite(message: types.Message):
    uid = message.from_user.id
    link = f"https://t.me/minglochat_bot?start={uid}"
    await message.answer(
        f"🎁 *Invite & Earn Premium*\n\n"
        f"🔗 {link}\n\n"
        f"👥 Invite 100 users → AUTO PREMIUM 💎",
        parse_mode="Markdown"
    )

@dp.message(lambda m: m.text == BTN_VIP)
async def vip(message: types.Message):
    user = await get_user(message.from_user.id)
    status = "💎 PREMIUM" if user["premium"] else "🆓 FREE"
    await message.answer(
        f"💎 *VIP STATUS*\n\n"
        f"Status: {status}\n"
        f"Referrals: {user['referrals']} / 100",
        parse_mode="Markdown"
    )

# ================= RELAY =================
@dp.message(lambda m: m.from_user.id in active and not m.text.startswith("/"))
async def relay(message: types.Message):
    await bot.send_message(active[message.from_user.id], message.text)

# ================= RUN =================
async def main():
    await init_db()
    print("🔥 Minglo Bot Running – Stable Version")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
