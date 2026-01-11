import os
import asyncio
import random
import asyncpg

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ================= DB =================
pool: asyncpg.Pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

    async with pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge_type TEXT
        );
        """)
        await con.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id BIGINT PRIMARY KEY
        );
        """)
        await con.execute("""
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id BIGINT,
            blocked_id BIGINT,
            PRIMARY KEY (user_id, blocked_id)
        );
        """)

# ---------- USER DB HELPERS ----------
async def get_user(uid):
    return await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid):
    await pool.execute(
        "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid
    )

async def update_user(uid, **data):
    if not data:
        return
    keys, values = [], []
    i = 1
    for k, v in data.items():
        keys.append(f"{k}=${i}")
        values.append(v)
        i += 1
    values.append(uid)
    query = f"UPDATE users SET {', '.join(keys)} WHERE user_id=${i}"
    await pool.execute(query, *values)

async def is_banned(uid):
    r = await pool.fetchrow(
        "SELECT 1 FROM banned_users WHERE user_id=$1", uid
    )
    return bool(r)

async def ban_user(uid):
    await pool.execute(
        "INSERT INTO banned_users VALUES ($1) ON CONFLICT DO NOTHING", uid
    )

async def unban_user(uid):
    await pool.execute(
        "DELETE FROM banned_users WHERE user_id=$1", uid
    )

async def block_user_db(uid, target):
    await pool.execute(
        "INSERT INTO blocked_users VALUES ($1,$2) ON CONFLICT DO NOTHING",
        uid, target
    )

async def unblock_user_db(uid, target):
    await pool.execute(
        "DELETE FROM blocked_users WHERE user_id=$1 AND blocked_id=$2",
        uid, target
    )

async def is_blocked(uid, target):
    r = await pool.fetchrow(
        "SELECT 1 FROM blocked_users WHERE user_id=$1 AND blocked_id=$2",
        uid, target
    )
    return bool(r)

# ================= RUNTIME DATA =================
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
skips = {}

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

# ================= KEYBOARDS =================
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔀 Random Chat (Free)")],
            [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
            [KeyboardButton(text="📢 Invite & Earn Premium")],
            [KeyboardButton(text="💎 VIP Status")],
            [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
            [KeyboardButton(text="🚫 Block & Report")]
        ],
        resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]],
        resize_keyboard=True
    )

# ================= UTIL =================
def mask_name(name):
    if not name:
        return "User"
    if len(name) <= 2:
        return name[0] + "*"
    return name[0] + "***" + name[-1]

async def check_ban(message):
    if await is_banned(message.from_user.id):
        await message.answer("🚫 You are banned.")
        return True
    return False

# ================= START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    if await check_ban(message): return
    uid = message.from_user.id
    args = message.text.split()

    await create_user(uid)
    user = await get_user(uid)

    if len(args) > 1 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            ruser = await get_user(ref)
            if ruser:
                await pool.execute(
                    "UPDATE users SET referrals = referrals + 1 WHERE user_id=$1",
                    ref
                )

    if not user["name"]:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ================= PROFILE =================
@dp.message(lambda m: True)
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user:
        return

    if not user["name"]:
        await update_user(uid, name=message.text)
        await message.answer("🎂 Age (18+)?")
        return

    if not user["age"]:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await update_user(uid, age=int(message.text))
        await message.answer("📍 Place?")
        return

    if not user["place"]:
        await update_user(uid, place=message.text)
        await message.answer("Select gender:", reply_markup=gender_keyboard())
        return

    if not user["gender"] and message.text in ["👦 Boy", "👧 Girl"]:
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed!", reply_markup=main_keyboard())
        return

# ================= MATCH =================
async def match_user(uid, pool_set, gender, message):
    search = await message.answer("🔎 Searching...")
    for other in list(pool_set):
        if other == uid:
            continue
        if await is_blocked(other, uid):
            continue
        o = await get_user(other)
        if not o or (gender and o["gender"] != gender):
            continue

        pool_set.discard(other)
        active_chats[uid] = other
        active_chats[other] = uid
        await search.delete()

        await bot.send_message(
            uid,
            f"🎉 Match found\nName: {mask_name(o['name'])}\nAge: {o['age']}\nPlace: {o['place']}",
            reply_markup=main_keyboard()
        )
        await bot.send_message(
            other,
            f"🎉 Match found\nName: {mask_name((await get_user(uid))['name'])}",
            reply_markup=main_keyboard()
        )
        return

    pool_set.add(uid)
    await search.edit_text("⏳ Waiting for partner...")

# ================= CHAT BUTTONS =================
@dp.message(lambda m: m.text == "🔀 Random Chat (Free)")
async def random_chat(message):
    if await check_ban(message): return
    await match_user(message.from_user.id, waiting_random, None, message)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def find_girls(message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_girls, "👧 Girl", message)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def find_boys(message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_boys, "👦 Boy", message)

# ================= RELAY =================
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message):
    uid = message.from_user.id
    pid = active_chats.get(uid)
    if pid:
        await bot.send_message(pid, message.text)

# ================= ADMIN =================
@dp.message(Command("admin"))
async def admin(message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👑 Admin Panel\n/send USER_ID to ban")

@dp.message(lambda m: m.from_user.id == ADMIN_ID and m.text.isdigit())
async def admin_ban(message):
    await ban_user(int(message.text))
    await message.answer("🚫 User banned")

# ================= RUN =================
async def main():
    await init_db()
    print("💜 Minglo Bot Running with PostgreSQL")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
