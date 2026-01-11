import os, asyncio, random, asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

PREMIUM_REFERRALS = 100
FREE_SKIP_LIMIT = 5

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db: asyncpg.Pool = None

# ================= MEMORY =================
waiting_random, waiting_girls, waiting_boys = set(), set(), set()
active_chats = {}
admin_state = {}

# ================= DEMO USERS =================
DEMO_USERS = {
    10001: ("Anu",21,"Kochi","👧 Girl"), 10002: ("Meera",22,"Calicut","👧 Girl"),
    10003: ("Aiswarya",23,"Trissur","👧 Girl"), 10004: ("Sneha",21,"Alappuzha","👧 Girl"),
    20001: ("Rahul",23,"Kochi","👦 Boy"), 20002: ("Arjun",24,"Calicut","👦 Boy"),
}
DEMO_REPLIES = ["Hi 🙂","Hello!","How are you?","🙂"]

# ================= DB INIT =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        name TEXT, age INT, place TEXT, gender TEXT,
        premium BOOLEAN DEFAULT FALSE,
        referrals INT DEFAULT 0,
        badge TEXT
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS referrals(
        referrer BIGINT,
        referred BIGINT UNIQUE
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS bans(user_id BIGINT PRIMARY KEY);
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS blocks(
        user_id BIGINT,
        blocked BIGINT
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS skips(
        user_id BIGINT PRIMARY KEY,
        count INT DEFAULT 0
    );
    """)

# ================= KEYBOARDS =================
def main_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="🔀 Random Chat (Free)")],
        [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
        [KeyboardButton(text="📢 Invite & Earn Premium")],
        [KeyboardButton(text="💎 VIP Status")],
        [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
        [KeyboardButton(text="🚫 Block & Report")]
    ])

def gender_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]
    ])

# ================= HELPERS =================
async def get_user(uid):
    return await db.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def is_banned(uid):
    return await db.fetchval("SELECT 1 FROM bans WHERE user_id=$1", uid)

def mask(name):
    return name[0] + "***" if name else "User"

# ================= START =================
@dp.message(Command("start"))
async def start(m: types.Message):
    uid = m.from_user.id
    if await is_banned(uid):
        return await m.answer("🚫 You are banned")

    if not await get_user(uid):
        await db.execute("INSERT INTO users(user_id) VALUES($1)", uid)

        args = m.text.split()
        if len(args) == 2 and args[1].isdigit():
            ref = int(args[1])
            if ref != uid and await get_user(ref):
                exists = await db.fetchval("SELECT 1 FROM referrals WHERE referred=$1", uid)
                if not exists:
                    await db.execute("INSERT INTO referrals VALUES($1,$2)", ref, uid)
                    await db.execute("UPDATE users SET referrals=referrals+1 WHERE user_id=$1", ref)
                    count = await db.fetchval("SELECT referrals FROM users WHERE user_id=$1", ref)
                    if count >= PREMIUM_REFERRALS:
                        await db.execute(
                            "UPDATE users SET premium=TRUE, badge='invite' WHERE user_id=$1", ref
                        )
                        await bot.send_message(ref, "🎉 AUTO PREMIUM UNLOCKED!")

    user = await get_user(uid)
    if not user["name"]:
        await m.answer("👤 Your name?")
    else:
        await m.answer("💜 Welcome back to Minglo!", reply_markup=main_kb())

# ================= PROFILE =================
@dp.message()
async def profile(m: types.Message):
    uid = m.from_user.id
    user = await get_user(uid)
    if not user or user["gender"]:
        return

    if not user["name"]:
        await db.execute("UPDATE users SET name=$1 WHERE user_id=$2", m.text, uid)
        return await m.answer("🎂 Age (18+)?")

    if not user["age"]:
        if not m.text.isdigit() or int(m.text) < 18:
            return await m.answer("❌ 18+ only")
        await db.execute("UPDATE users SET age=$1 WHERE user_id=$2", int(m.text), uid)
        return await m.answer("📍 Place?")

    if not user["place"]:
        await db.execute("UPDATE users SET place=$1 WHERE user_id=$2", m.text, uid)
        return await m.answer("Select gender", reply_markup=gender_kb())

    if m.text in ["👦 Boy","👧 Girl"]:
        await db.execute("UPDATE users SET gender=$1 WHERE user_id=$2", m.text, uid)
        await m.answer("✅ Profile completed!", reply_markup=main_kb())

# ================= MATCH ENGINE =================
async def match(uid, pool, gender, m):
    if uid in active_chats:
        return await m.answer("❌ Already in chat")

    wait = await m.answer("🔎 Searching...")

    for u in list(pool):
        if u != uid and u not in active_chats:
            pool.remove(u)
            active_chats[uid] = u
            active_chats[u] = uid
            await wait.delete()

            me = await get_user(uid)
            you = await get_user(u)

            await bot.send_message(u,
                f"🎉 Match found!\nName: {mask(me['name'])}\nAge: {me['age']}\nPlace: {me['place']}",
                reply_markup=main_kb())
            await m.answer(
                f"🎉 Match found!\nName: {mask(you['name'])}\nAge: {you['age']}\nPlace: {you['place']}",
                reply_markup=main_kb())
            return

    pool.add(uid)
    await wait.edit_text("⏳ Waiting for a partner...")

# ================= BUTTONS =================
@dp.message(lambda m: m.text=="🔀 Random Chat (Free)")
async def random_chat(m): await match(m.from_user.id, waiting_random, None, m)

@dp.message(lambda m: m.text=="👧 Find Girls")
async def girls(m):
    if not (await get_user(m.from_user.id))["premium"]:
        return await m.answer("💎 Premium required")
    await match(m.from_user.id, waiting_girls, "👧 Girl", m)

@dp.message(lambda m: m.text=="👦 Find Boys")
async def boys(m):
    if not (await get_user(m.from_user.id))["premium"]:
        return await m.answer("💎 Premium required")
    await match(m.from_user.id, waiting_boys, "👦 Boy", m)

@dp.message(lambda m: m.text=="⏭ Next")
async def next_chat(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid,None)
        await bot.send_message(pid,"❌ Partner skipped")
    await random_chat(m)

@dp.message(lambda m: m.text=="❌ Stop")
async def stop(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid,None)
        await bot.send_message(pid,"❌ Chat ended")
    await m.answer("✅ Stopped", reply_markup=main_kb())

# ================= RELAY =================
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(m):
    pid = active_chats.get(m.from_user.id)
    if pid:
        await bot.send_message(pid, m.text)

# ================= ADMIN =================
@dp.message(Command("admin"))
async def admin(m):
    if m.from_user.id != ADMIN_ID: return
    total = await db.fetchval("SELECT COUNT(*) FROM users")
    premium = await db.fetchval("SELECT COUNT(*) FROM users WHERE premium=TRUE")
    await m.answer(f"👥 Users: {total}\n💎 Premium: {premium}")

# ================= RUN =================
async def main():
    await init_db()
    print("🔥 Minglo DB Bot Running")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
