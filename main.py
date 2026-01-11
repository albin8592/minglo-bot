import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- RUNTIME DATA ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
skips = {}
admin_state = {}

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

db: asyncpg.Pool = None

# ---------------- DATABASE ----------------
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id BIGINT PRIMARY KEY,
        name TEXT,
        age INT,
        place TEXT,
        gender TEXT,
        premium BOOLEAN DEFAULT FALSE,
        referrals INT DEFAULT 0,
        badge_type TEXT
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS bans(
        user_id BIGINT PRIMARY KEY
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS blocks(
        user_id BIGINT,
        blocked_id BIGINT
    );
    """)

# ---------------- HELPERS ----------------
async def is_banned(uid: int) -> bool:
    row = await db.fetchrow("SELECT 1 FROM bans WHERE user_id=$1", uid)
    return row is not None

async def get_user(uid: int):
    return await db.fetchrow("SELECT * FROM users WHERE id=$1", uid)

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔀 Random Chat")],
            [KeyboardButton(text="👧 Find Girls (VIP)")],
            [KeyboardButton(text="👦 Find Boys (VIP)")],
            [KeyboardButton(text="💎 VIP Status")],
            [KeyboardButton(text="📢 Invite & Earn")],
            [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")]
        ],
        resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]],
        resize_keyboard=True
    )

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id

    if await is_banned(uid):
        await message.answer("🚫 You are banned.")
        return

    user = await get_user(uid)

    if not user:
        await db.execute(
            "INSERT INTO users(id) VALUES($1)",
            uid
        )
        skips[uid] = 0
        await message.answer("👤 Your name?")
        return

    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE SETUP ----------------
@dp.message(lambda m: True)
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)

    if not user or await is_banned(uid):
        return

    if user["name"] is None:
        await db.execute("UPDATE users SET name=$1 WHERE id=$2", message.text, uid)
        await message.answer("🎂 Age (18+)?")
        return

    if user["age"] is None:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await db.execute("UPDATE users SET age=$1 WHERE id=$2", int(message.text), uid)
        await message.answer("📍 Place?")
        return

    if user["place"] is None:
        await db.execute("UPDATE users SET place=$1 WHERE id=$2", message.text, uid)
        await message.answer("Select gender", reply_markup=gender_keyboard())
        return

    if user["gender"] is None:
        if message.text not in ["👦 Boy", "👧 Girl"]:
            return
        await db.execute("UPDATE users SET gender=$1 WHERE id=$2", message.text, uid)
        await message.answer("✅ Profile completed!", reply_markup=main_keyboard())
        return

# ---------------- MATCHING ----------------
def mask(name):
    if not name: return "User"
    return name[0] + "***"

async def match(uid, pool, target_gender, message):
    searching = await message.answer("🔎 Searching premium match...")

    for pid in list(pool):
        if pid == uid:
            continue

        partner = await get_user(pid)
        if not partner:
            continue

        if target_gender and partner["gender"] != target_gender:
            continue

        pool.remove(pid)
        active_chats[uid] = pid
        active_chats[pid] = uid

        await searching.delete()

        await bot.send_message(
            uid,
            f"💖 Match Found!\nName: {mask(partner['name'])}\nAge: {partner['age']}\nPlace: {partner['place']}",
            reply_markup=main_keyboard()
        )

        await bot.send_message(
            pid,
            f"💖 Match Found!\nName: {mask((await get_user(uid))['name'])}",
            reply_markup=main_keyboard()
        )
        return

    pool.add(uid)

# ---------------- COMMANDS ----------------
@dp.message(lambda m: m.text == "🔀 Random Chat")
async def random_chat(message: types.Message):
    await match(message.from_user.id, waiting_random, None, message)

@dp.message(lambda m: m.text == "👧 Find Girls (VIP)")
async def girls(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 VIP only feature")
        return
    await match(message.from_user.id, waiting_girls, "👧 Girl", message)

@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Partner skipped")
    await random_chat(message)

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats)
async def relay(message: types.Message):
    pid = active_chats[message.from_user.id]
    await bot.send_message(pid, message.text)

# ---------------- ADMIN ----------------
@dp.message(Command("admin"))
async def admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👑 Admin Panel\n/ban ID\n/unban ID\n/vip ID")

@dp.message(Command("ban"))
async def ban(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.text.split()[1])
    await db.execute("INSERT INTO bans(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
    await message.answer("🚫 User banned")

@dp.message(Command("vip"))
async def vip(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    uid = int(message.text.split()[1])
    await db.execute("UPDATE users SET premium=TRUE, badge_type='admin' WHERE id=$1", uid)
    await message.answer("👑 VIP granted")

# ---------------- RUN ----------------
async def main():
    await init_db()
    print("🚀 Bot running on Railway with DB")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
