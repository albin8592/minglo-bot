import os
import asyncio
import random
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB = "bot.db"

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
admin_state = {}

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

# ---------------- DB INIT ----------------
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            age INTEGER,
            place TEXT,
            gender TEXT,
            premium INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            badge_type TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS banned (
            user_id INTEGER PRIMARY KEY
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS blocked (
            user_id INTEGER,
            blocked_id INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS skips (
            user_id INTEGER PRIMARY KEY,
            count INTEGER DEFAULT 0
        )""")
        await db.commit()

# ---------------- HELPERS ----------------
async def is_banned(uid):
    async with aiosqlite.connect(DB) as db:
        c = await db.execute("SELECT 1 FROM banned WHERE user_id=?", (uid,))
        return await c.fetchone() is not None

async def get_user(uid):
    async with aiosqlite.connect(DB) as db:
        c = await db.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        r = await c.fetchone()
        if not r:
            return None
        return {
            "user_id": r[0], "name": r[1], "age": r[2],
            "place": r[3], "gender": r[4],
            "premium": bool(r[5]), "referrals": r[6],
            "badge_type": r[7]
        }

async def save_user(uid, **data):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
        for k, v in data.items():
            await db.execute(f"UPDATE users SET {k}=? WHERE user_id=?", (v, uid))
        await db.execute("INSERT OR IGNORE INTO skips (user_id) VALUES (?)", (uid,))
        await db.commit()

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

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    if await is_banned(uid):
        await message.answer("🚫 You are banned")
        return

    user = await get_user(uid)
    if not user:
        await save_user(uid)

    if not user or user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE SETUP ----------------
@dp.message(lambda m: True)
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    if await is_banned(uid): return
    user = await get_user(uid)
    if not user: return

    if user["name"] is None:
        await save_user(uid, name=message.text)
        await message.answer("🎂 Age (18+)?")
        return

    if user["age"] is None:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await save_user(uid, age=int(message.text))
        await message.answer("📍 Place?")
        return

    if user["place"] is None:
        await save_user(uid, place=message.text)
        await message.answer("Select gender:", reply_markup=gender_keyboard())
        return

    if user["gender"] is None:
        if message.text not in ["👦 Boy", "👧 Girl"]: return
        await save_user(uid, gender=message.text)
        await message.answer("✅ Profile completed!", reply_markup=main_keyboard())

# ---------------- MATCH ----------------
def mask(name):
    return name[0] + "***" if name else "User"

async def match_user(uid, pool, gender, message):
    await message.answer("🔎 Searching...")
    async with aiosqlite.connect(DB) as db:
        q = "SELECT user_id,name,age,place FROM users WHERE user_id!=?"
        params = [uid]
        if gender:
            q += " AND gender=?"
            params.append(gender)
        c = await db.execute(q, params)
        users = await c.fetchall()

    random.shuffle(users)
    for u in users:
        pid = u[0]
        if pid not in active_chats:
            active_chats[uid] = pid
            active_chats[pid] = uid
            await message.answer(
                f"🎉 Match found!\nName: {mask(u[1])}\nAge: {u[2]}\nPlace: {u[3]}",
                reply_markup=main_keyboard()
            )
            await bot.send_message(pid, "🎉 Match found!", reply_markup=main_keyboard())
            return

    pool.add(uid)
    await message.answer("⏳ Waiting for partner...")

# ---------------- CHAT BUTTONS ----------------
@dp.message(lambda m: m.text == "🔀 Random Chat (Free)")
async def random_chat(message: types.Message):
    await match_user(message.from_user.id, waiting_random, None, message)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def girls_chat(message: types.Message):
    u = await get_user(message.from_user.id)
    if not u["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_girls, "👧 Girl", message)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def boys_chat(message: types.Message):
    u = await get_user(message.from_user.id)
    if not u["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_boys, "👦 Boy", message)

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    pid = active_chats.get(message.from_user.id)
    if pid:
        await bot.send_message(pid, message.text)

# ---------------- RUN ----------------
async def main():
    await init_db()
    print("✅ Minglo Bot DB version running")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
