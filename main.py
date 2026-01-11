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

# ================= ENV =================
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db: asyncpg.Pool = None

# ============== MEMORY (FAST) ==========
active_chats = {}
waiting_random = set()
waiting_girls = set()
waiting_boys = set()

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

admin_state = {}

# ================= DB INIT =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with db.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge_type TEXT,
            skips INT DEFAULT 0
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

# ================= DB HELPERS =================
async def get_user(uid):
    async with db.acquire() as con:
        return await con.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid):
    async with db.acquire() as con:
        await con.execute("INSERT INTO users (user_id) VALUES ($1)", uid)

async def update_user(uid, **kwargs):
    keys = ", ".join(f"{k}=${i+2}" for i, k in enumerate(kwargs))
    vals = list(kwargs.values())
    async with db.acquire() as con:
        await con.execute(f"UPDATE users SET {keys} WHERE user_id=$1", uid, *vals)

async def is_banned(uid):
    async with db.acquire() as con:
        return await con.fetchval("SELECT 1 FROM banned_users WHERE user_id=$1", uid)

async def ban_user(uid):
    async with db.acquire() as con:
        await con.execute(
            "INSERT INTO banned_users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
            uid
        )

async def unban_user(uid):
    async with db.acquire() as con:
        await con.execute("DELETE FROM banned_users WHERE user_id=$1", uid)

async def block_user_db(uid, target):
    async with db.acquire() as con:
        await con.execute(
            "INSERT INTO blocked_users VALUES ($1,$2) ON CONFLICT DO NOTHING",
            uid, target
        )

async def is_blocked(uid, target):
    async with db.acquire() as con:
        return await con.fetchval(
            "SELECT 1 FROM blocked_users WHERE user_id=$1 AND blocked_id=$2",
            uid, target
        )

# ================= KEYBOARDS =================
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔀 Random Chat (Free)")],
            [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
            [KeyboardButton(text="📢 Invite & Earn Premium")],
            [KeyboardButton(text="💎 VIP Status")],
            [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
            [KeyboardButton(text="🚫 Block")]
        ],
        resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]],
        resize_keyboard=True
    )

# ================= START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    if await is_banned(uid):
        await message.answer("🚫 You are banned")
        return

    user = await get_user(uid)
    if not user:
        await create_user(uid)
        await message.answer("👤 Your name?")
        return

    if not user["name"]:
        await message.answer("👤 Your name?")
        return

    await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ================= PROFILE FLOW =================
@dp.message(lambda m: True)
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user or user["gender"]:
        return

    if not user["name"]:
        await update_user(uid, name=message.text)
        await message.answer("🎂 Age?")
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

    if message.text in ["👦 Boy", "👧 Girl"]:
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed", reply_markup=main_keyboard())

# ================= MATCH =================
def mask(name): return name[0] + "***"

async def match_user(uid, pool, target_gender, message):
    search = await message.answer("🔎 Searching...")

    async with db.acquire() as con:
        users = await con.fetch("""
        SELECT * FROM users
        WHERE user_id != $1
        AND gender IS NOT NULL
        AND ($2::TEXT IS NULL OR gender=$2)
        """, uid, target_gender)

    random.shuffle(users)
    for u in users:
        pid = u["user_id"]
        if pid in active_chats:
            continue
        if await is_blocked(uid, pid) or await is_blocked(pid, uid):
            continue

        active_chats[uid] = pid
        active_chats[pid] = uid
        await search.delete()

        await message.answer(
            f"🎉 Match found!\nName: {mask(u['name'])}\nAge: {u['age']}\nPlace: {u['place']}",
            reply_markup=main_keyboard()
        )
        return

    pool.add(uid)
    await search.edit_text("⏳ Waiting for partner...")

# ================= CHAT =================
@dp.message(lambda m: m.text == "🔀 Random Chat (Free)")
async def random_chat(message: types.Message):
    await match_user(message.from_user.id, waiting_random, None, message)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def girls(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_girls, "👧 Girl", message)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def boys(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_boys, "👦 Boy", message)

@dp.message(lambda m: m.text == "🚫 Block")
async def block(message: types.Message):
    uid = message.from_user.id
    pid = active_chats.pop(uid, None)
    if pid:
        active_chats.pop(pid, None)
        await block_user_db(uid, pid)
        await bot.send_message(pid, "🚫 You were blocked")
    await message.answer("User blocked")

@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    pid = active_chats.get(message.from_user.id)
    if pid:
        await bot.send_message(pid, message.text)

# ================= ADMIN =================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="broadcast")],
        [InlineKeyboardButton(text="🚫 Ban", callback_data="ban")],
        [InlineKeyboardButton(text="✅ Unban", callback_data="unban")],
        [InlineKeyboardButton(text="👑 Give Premium", callback_data="premium")],
    ])
    await message.answer("Admin Panel", reply_markup=kb)

@dp.callback_query()
async def admin_cb(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    admin_state[ADMIN_ID] = cb.data
    await cb.message.answer(f"Send user ID / message for {cb.data}")
    await cb.answer()

@dp.message(lambda m: m.from_user.id == ADMIN_ID and ADMIN_ID in admin_state)
async def admin_actions(message: types.Message):
    action = admin_state.pop(ADMIN_ID)

    if action == "broadcast":
        async with db.acquire() as con:
            users = await con.fetch("SELECT user_id FROM users")
        for u in users:
            try:
                await message.copy_to(u["user_id"])
            except:
                pass
        await message.answer("✅ Broadcast done")

    elif action == "ban":
        await ban_user(int(message.text))
        await message.answer("User banned")

    elif action == "unban":
        await unban_user(int(message.text))
        await message.answer("User unbanned")

    elif action == "premium":
        await update_user(int(message.text), premium=True, badge_type="admin")
        await message.answer("Premium granted")

# ================= MAIN =================
async def main():
    await init_db()
    print("✅ Minglo FULL DB BOT RUNNING")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
