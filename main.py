import os, asyncio, random, time
import asyncpg, redis
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import *
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db: asyncpg.Pool = None
rdb = redis.Redis.from_url(REDIS_URL, decode_responses=True)

active_chats = {}
admin_state = {}

# ---------------- DB INIT ----------------
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)
    async with db.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            skips INT DEFAULT 0,
            badge TEXT,
            banned BOOLEAN DEFAULT FALSE
        );
        CREATE TABLE IF NOT EXISTS blocks(
            user_id BIGINT,
            blocked_id BIGINT,
            PRIMARY KEY(user_id, blocked_id)
        );
        CREATE TABLE IF NOT EXISTS messages_log(
            id SERIAL PRIMARY KEY,
            sender BIGINT,
            receiver BIGINT,
            text TEXT,
            created TIMESTAMP DEFAULT NOW()
        );
        """)

# ---------------- DB HELPERS ----------------
async def get_user(uid):
    async with db.acquire() as con:
        r = await con.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        return dict(r) if r else None

async def create_user(uid):
    async with db.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid
        )

async def update(uid, field, val):
    async with db.acquire() as con:
        await con.execute(f"UPDATE users SET {field}=$1 WHERE user_id=$2", val, uid)

async def is_blocked(uid, pid):
    async with db.acquire() as con:
        r = await con.fetchrow(
            "SELECT 1 FROM blocks WHERE user_id=$1 AND blocked_id=$2",
            uid, pid
        )
        return bool(r)

# ---------------- KEYBOARDS ----------------
def main_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="🔀 Random Chat (Free)")],
        [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
        [KeyboardButton(text="📢 Invite & Earn Premium")],
        [KeyboardButton(text="💎 VIP Status")],
        [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
        [KeyboardButton(text="🚫 Block & Report"), KeyboardButton(text="✅ Unblock")]
    ])

def gender_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]
    ])

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(m: types.Message):
    uid = m.from_user.id
    await create_user(uid)
    user = await get_user(uid)

    if user["banned"]:
        await m.answer("🚫 You are banned.")
        return

    if user["name"] is None:
        await m.answer("👤 Your name?")
    else:
        await m.answer("💜 Welcome back!", reply_markup=main_kb())

# ---------------- PROFILE FLOW ----------------
@dp.message(lambda m: True)
async def profile(m: types.Message):
    uid = m.from_user.id
    user = await get_user(uid)
    if not user or user["banned"]:
        return

    if user["name"] is None:
        await update(uid, "name", m.text)
        await m.answer("🎂 Age (18+)?")
        return

    if user["age"] is None:
        if not m.text.isdigit() or int(m.text) < 18:
            await m.answer("❌ 18+ only")
            return
        await update(uid, "age", int(m.text))
        await m.answer("📍 Place?")
        return

    if user["place"] is None:
        await update(uid, "place", m.text)
        await m.answer("Gender?", reply_markup=gender_kb())
        return

    if user["gender"] is None:
        if m.text not in ["👦 Boy", "👧 Girl"]:
            return
        await update(uid, "gender", m.text)
        await m.answer("✅ Profile completed!", reply_markup=main_kb())
        return

# ---------------- MATCH ENGINE (REDIS) ----------------
async def try_match(uid, queue, target_gender=None):
    user = await get_user(uid)

    for pid in rdb.smembers(queue):
        pid = int(pid)
        if pid == uid or pid in active_chats:
            rdb.srem(queue, pid)
            continue

        puser = await get_user(pid)
        if not puser or puser["banned"]:
            rdb.srem(queue, pid)
            continue

        if target_gender and puser["gender"] != target_gender:
            continue

        if await is_blocked(uid, pid) or await is_blocked(pid, uid):
            continue

        rdb.srem(queue, pid)
        active_chats[uid] = pid
        active_chats[pid] = uid

        await bot.send_message(uid, "🎉 Match Found!", reply_markup=main_kb())
        await bot.send_message(pid, "🎉 Match Found!", reply_markup=main_kb())
        return

    rdb.sadd(queue, uid)
    await bot.send_message(uid, "⏳ Waiting for partner...")

# ---------------- CHAT BUTTONS ----------------
@dp.message(lambda m: m.text == "🔀 Random Chat (Free)")
async def random_chat(m: types.Message):
    await try_match(m.from_user.id, "wait:random")

@dp.message(lambda m: m.text == "👧 Find Girls")
async def girls(m: types.Message):
    u = await get_user(m.from_user.id)
    if not u["premium"]:
        await m.answer("💎 Premium required")
        return
    await try_match(m.from_user.id, "wait:girls", "👧 Girl")

@dp.message(lambda m: m.text == "👦 Find Boys")
async def boys(m: types.Message):
    u = await get_user(m.from_user.id)
    if not u["premium"]:
        await m.answer("💎 Premium required")
        return
    await try_match(m.from_user.id, "wait:boys", "👦 Boy")

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats)
async def relay(m: types.Message):
    uid = m.from_user.id
    pid = active_chats.get(uid)
    if not pid:
        return

    async with db.acquire() as con:
        await con.execute(
            "INSERT INTO messages_log(sender,receiver,text) VALUES($1,$2,$3)",
            uid, pid, m.text
        )

    await bot.send_message(pid, m.text)

# ---------------- STOP ----------------
@dp.message(lambda m: m.text == "❌ Stop")
async def stop(m: types.Message):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Chat ended")
    await m.answer("✅ Stopped")

# ---------------- BLOCK ----------------
@dp.message(lambda m: m.text == "🚫 Block & Report")
async def block(m: types.Message):
    uid = m.from_user.id
    pid = active_chats.get(uid)
    if not pid:
        return
    async with db.acquire() as con:
        await con.execute(
            "INSERT INTO blocks(user_id,blocked_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
            uid, pid
        )
    await stop(m)

# ---------------- ADMIN ----------------
@dp.message(Command("admin"))
async def admin(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="bc")],
        [InlineKeyboardButton(text="🚫 Ban", callback_data="ban")],
        [InlineKeyboardButton(text="👑 VIP", callback_data="vip")]
    ])
    await m.answer("Admin Panel", reply_markup=kb)

@dp.callback_query(lambda c: c.data in ["bc","ban","vip"])
async def admin_cb(c: CallbackQuery):
    admin_state[ADMIN_ID] = c.data
    await c.message.answer("Send user id / message")
    await c.answer()

@dp.message(lambda m: m.from_user.id==ADMIN_ID and ADMIN_ID in admin_state)
async def admin_action(m: types.Message):
    act = admin_state.pop(ADMIN_ID)

    if act=="ban":
        await update(int(m.text),"banned",True)
        await m.answer("🚫 Banned")

    elif act=="vip":
        await update(int(m.text),"premium",True)
        await update(int(m.text),"badge","admin")
        await m.answer("👑 VIP given")

    elif act=="bc":
        async with db.acquire() as con:
            users = await con.fetch("SELECT user_id FROM users WHERE banned=FALSE")
        for u in users:
            try: await m.copy_to(u["user_id"])
            except: pass

# ---------------- RUN ----------------
async def main():
    await init_db()
    print("🚀 DB + REDIS BOT RUNNING")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
