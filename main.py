import os, asyncio, random, asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------- CONFIG ----------------
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

# ---------------- DATABASE ----------------
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as con:
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
        )""")

        await con.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id BIGINT PRIMARY KEY
        )""")

        await con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            sender BIGINT,
            receiver BIGINT,
            message TEXT,
            time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

# ---------------- DB HELPERS ----------------
async def get_user(uid):
    async with db_pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        return dict(row) if row else None

async def add_user(uid):
    async with db_pool.acquire() as con:
        await con.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid
        )

async def update_user(uid, field, value):
    async with db_pool.acquire() as con:
        await con.execute(
            f"UPDATE users SET {field}=$1 WHERE user_id=$2", value, uid
        )

async def log_message(sender, receiver, text):
    async with db_pool.acquire() as con:
        await con.execute(
            "INSERT INTO messages (sender, receiver, message) VALUES ($1,$2,$3)",
            sender, receiver, text
        )

async def ban_user(uid):
    async with db_pool.acquire() as con:
        await con.execute(
            "INSERT INTO banned_users VALUES ($1) ON CONFLICT DO NOTHING", uid
        )

async def unban_user(uid):
    async with db_pool.acquire() as con:
        await con.execute(
            "DELETE FROM banned_users WHERE user_id=$1", uid
        )

async def is_banned(uid):
    async with db_pool.acquire() as con:
        return await con.fetchval(
            "SELECT 1 FROM banned_users WHERE user_id=$1", uid
        ) is not None

# ---------------- MEMORY ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
blocked = {}
skips = {}
admin_state = {}

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="🔀 Random Chat (Free)")],
        [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
        [KeyboardButton(text="📢 Invite & Earn Premium")],
        [KeyboardButton(text="💎 VIP Status")],
        [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
        [KeyboardButton(text="🚫 Block & Report"), KeyboardButton(text="✅ Unblock")]
    ])

def gender_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]
    ])

# ---------------- BAN CHECK ----------------
async def check_banned(message):
    if await is_banned(message.from_user.id):
        await message.answer("🚫 You are banned.")
        return True
    return False

# ---------------- START + REFERRAL ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    if await check_banned(message): return

    uid = message.from_user.id
    args = message.text.split()
    await add_user(uid)

    # referral logic
    if len(args) > 1 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            async with db_pool.acquire() as con:
                await con.execute(
                    "UPDATE users SET referrals=referrals+1 WHERE user_id=$1", ref
                )
                count = await con.fetchval(
                    "SELECT referrals FROM users WHERE user_id=$1", ref
                )
                if count >= PREMIUM_REFERRALS:
                    await con.execute(
                        "UPDATE users SET premium=TRUE, badge_type='invite' WHERE user_id=$1", ref
                    )

    user = await get_user(uid)
    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE FLOW ----------------
@dp.message(lambda m: True)
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    if await check_banned(message): return

    user = await get_user(uid)
    if not user: return

    if user["name"] is None:
        await update_user(uid, "name", message.text)
        await message.answer("🎂 Age (18+)?")
        return

    if user["age"] is None:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await update_user(uid, "age", int(message.text))
        await message.answer("📍 Place?")
        return

    if user["place"] is None:
        await update_user(uid, "place", message.text)
        await message.answer("Select gender:", reply_markup=gender_keyboard())
        return

    if user["gender"] is None:
        if message.text not in ["👦 Boy", "👧 Girl"]:
            return
        await update_user(uid, "gender", message.text)
        await message.answer("✅ Profile completed", reply_markup=main_keyboard())

# ---------------- MATCH ----------------
def mask(name): return name[0]+"***" if name else "User"

async def match_user(uid, pool, gender, message):
    msg = await message.answer("🔎 Searching...")
    for u in list(pool):
        if u != uid and u not in active_chats:
            user = await get_user(u)
            if user and (gender is None or user["gender"] == gender):
                pool.discard(u)
                active_chats[uid] = u
                active_chats[u] = uid
                await msg.delete()
                await bot.send_message(uid, f"🎉 Match: {mask(user['name'])}", reply_markup=main_keyboard())
                return
    pool.add(uid)
    await msg.edit_text("⏳ Waiting...")

# ---------------- CHAT BUTTONS ----------------
@dp.message(lambda m: m.text in ["🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys"])
async def start_chat(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)

    if message.text == "🔀 Random Chat (Free)":
        await match_user(uid, waiting_random, None, message)

    if message.text == "👧 Find Girls":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        await match_user(uid, waiting_girls, "👧 Girl", message)

    if message.text == "👦 Find Boys":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        await match_user(uid, waiting_boys, "👦 Boy", message)

# ---------------- RELAY + LOG ----------------
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    uid = message.from_user.id
    pid = active_chats.get(uid)
    if pid:
        await log_message(uid, pid, message.text)
        await bot.send_message(pid, message.text)

# ---------------- ADMIN PANEL ----------------
@dp.message(Command("admin"))
async def admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Not admin")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="bc")],
        [InlineKeyboardButton(text="🚫 Ban", callback_data="ban")],
        [InlineKeyboardButton(text="✅ Unban", callback_data="unban")],
        [InlineKeyboardButton(text="👑 Give VIP", callback_data="vip")]
    ])
    await message.answer("Admin Panel", reply_markup=kb)

@dp.callback_query(lambda c: c.data in ["bc","ban","unban","vip"])
async def admin_cb(c: CallbackQuery):
    admin_state[ADMIN_ID] = c.data
    await c.message.answer("Send user id / message")
    await c.answer()

@dp.message(lambda m: m.from_user.id == ADMIN_ID and ADMIN_ID in admin_state)
async def admin_action(message: types.Message):
    act = admin_state.pop(ADMIN_ID)
    if act == "ban":
        await ban_user(int(message.text))
        await message.answer("🚫 Banned")
    elif act == "unban":
        await unban_user(int(message.text))
        await message.answer("✅ Unbanned")
    elif act == "vip":
        await update_user(int(message.text), "premium", True)
        await update_user(int(message.text), "badge_type", "admin")
        await message.answer("👑 VIP granted")
    elif act == "bc":
        async with db_pool.acquire() as con:
            users = await con.fetch("SELECT user_id FROM users")
            for u in users:
                try:
                    await message.copy_to(u["user_id"])
                except: pass

# ---------------- RUN ----------------
async def main():
    await init_db()
    print("🚀 Minglo Bot Running (PostgreSQL)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
