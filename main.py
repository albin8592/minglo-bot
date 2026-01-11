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

# ---------------- CONFIG ----------------
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- RUNTIME (MEMORY OK) ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
skips = {}
admin_state = {}
demo_active = set()
demo_reply_count = {}

# ---------------- DEMO USERS ----------------
DEMO_USERS = {
    10001: {"name": "Anu", "age": 21, "place": "Kochi", "gender": "👧 Girl"},
    10002: {"name": "Meera", "age": 22, "place": "Calicut", "gender": "👧 Girl"},
    20001: {"name": "Rahul", "age": 23, "place": "Kochi", "gender": "👦 Boy"},
    20002: {"name": "Arjun", "age": 24, "place": "Calicut", "gender": "👦 Boy"},
}

DEMO_REPLIES = [
    "Hi 🙂", "Hello!", "How are you?", "Nice to meet you",
    "Where are you from?", "🙂", "That's nice", "Haha 😄"
]

# ---------------- DB ----------------
db_pool = None

async def connect_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    async with db_pool.acquire() as conn:
        await conn.execute("""
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

        CREATE TABLE IF NOT EXISTS banned_users (
            user_id BIGINT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id BIGINT,
            blocked_id BIGINT
        );
        """)

async def create_user(uid):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING",
            uid
        )

async def get_user(uid):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def update_user(uid, **kwargs):
    if not kwargs:
        return
    keys = list(kwargs.keys())
    vals = list(kwargs.values())
    sets = ", ".join(f"{k}=${i+1}" for i, k in enumerate(keys))
    async with db_pool.acquire() as conn:
        await conn.execute(
            f"UPDATE users SET {sets} WHERE user_id=${len(vals)+1}",
            *vals, uid
        )

async def is_banned(uid):
    async with db_pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT 1 FROM banned_users WHERE user_id=$1", uid
        )
        return r is not None

async def is_blocked(uid, target):
    async with db_pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT 1 FROM blocked_users WHERE user_id=$1 AND blocked_id=$2",
            uid, target
        )
        return r is not None

# ---------------- DEMO LOAD ----------------
async def load_demo_users():
    async with db_pool.acquire() as conn:
        for uid, d in DEMO_USERS.items():
            await conn.execute("""
            INSERT INTO users (user_id,name,age,place,gender,premium)
            VALUES ($1,$2,$3,$4,$5,true)
            ON CONFLICT (user_id) DO NOTHING
            """, uid, d["name"], d["age"], d["place"], d["gender"])
            demo_active.add(uid)

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔀 Random Chat (Free)")],
            [KeyboardButton(text="👧 Find Girls")],
            [KeyboardButton(text="👦 Find Boys")],
            [KeyboardButton(text="📢 Invite & Earn Premium")],
            [KeyboardButton(text="💎 VIP Status")],
            [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
            [KeyboardButton(text="🚫 Block & Report"), KeyboardButton(text="✅ Unblock")]
        ],
        resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]],
        resize_keyboard=True
    )

# ---------------- COMMON ----------------
async def check_banned(message):
    if await is_banned(message.from_user.id):
        await message.answer("🚫 You are banned.")
        return True
    return False

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    if await check_banned(message): return

    uid = message.from_user.id
    args = message.text.split()

    await create_user(uid)
    user = await get_user(uid)
    skips.setdefault(uid, 0)

    # referral
    if len(args) > 1 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            async with db_pool.acquire() as conn:
                if await conn.fetchrow("SELECT 1 FROM users WHERE user_id=$1", ref):
                    await conn.execute(
                        "UPDATE users SET referrals = referrals + 1 WHERE user_id=$1",
                        ref
                    )
                    count = await conn.fetchval(
                        "SELECT referrals FROM users WHERE user_id=$1", ref
                    )
                    if count >= PREMIUM_REFERRALS:
                        await conn.execute(
                            "UPDATE users SET premium=true, badge_type='invite' WHERE user_id=$1",
                            ref
                        )

    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE ----------------
@dp.message()
async def profile_flow(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    user = await get_user(uid)
    if not user: return

    if user["name"] is None:
        await update_user(uid, name=message.text)
        await message.answer("🎂 Age (18+)?")
        return

    if user["age"] is None:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await update_user(uid, age=int(message.text))
        await message.answer("📍 Place?")
        return

    if user["place"] is None:
        await update_user(uid, place=message.text)
        await message.answer("Select gender", reply_markup=gender_keyboard())
        return

    if user["gender"] is None:
        if message.text not in ["👦 Boy", "👧 Girl"]: return
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed", reply_markup=main_keyboard())

# ---------------- MATCH ----------------
def mask_name(name):
    return name[0] + "***" if name else "User"

async def demo_auto_reply(demo, uid):
    await asyncio.sleep(random.randint(2, 5))
    await bot.send_message(uid, random.choice(DEMO_REPLIES))

async def match_user(uid, pool, target_gender, message, allow_demo=True):
    await message.answer("🔎 Searching...")
    random.shuffle(list(pool))

    for partner in list(pool):
        if partner == uid or partner in active_chats:
            continue
        p = await get_user(partner)
        u = await get_user(uid)
        if not p or not u: continue
        if target_gender and p["gender"] != target_gender:
            continue
        if await is_blocked(partner, uid):
            continue

        pool.discard(partner)
        active_chats[uid] = partner
        active_chats[partner] = uid

        await bot.send_message(
            uid,
            f"🎉 Match found!\nName: {mask_name(p['name'])}\nAge: {p['age']}\nPlace: {p['place']}",
            reply_markup=main_keyboard()
        )
        await bot.send_message(
            partner,
            f"🎉 Match found!\nName: {mask_name(u['name'])}\nAge: {u['age']}\nPlace: {u['place']}",
            reply_markup=main_keyboard()
        )
        return

    if allow_demo:
        demo = random.choice(list(demo_active))
        active_chats[uid] = demo
        await message.answer(
            f"🎉 Match found!\nName: {mask_name(DEMO_USERS[demo]['name'])}",
            reply_markup=main_keyboard()
        )
        asyncio.create_task(demo_auto_reply(demo, uid))
        return

    pool.add(uid)
    await message.answer("⏳ Waiting for partner...")

# ---------------- CHAT BUTTONS ----------------
@dp.message(lambda m: m.text in ["🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys"])
async def start_chat(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    user = await get_user(uid)

    if uid in active_chats:
        await message.answer("❌ Already in chat")
        return

    if message.text == "🔀 Random Chat (Free)":
        await match_user(uid, waiting_random, None, message)

    elif message.text == "👧 Find Girls":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        await match_user(uid, waiting_girls, "👧 Girl", message, False)

    elif message.text == "👦 Find Boys":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        await match_user(uid, waiting_boys, "👦 Boy", message, False)

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    uid = message.from_user.id
    pid = active_chats.get(uid)
    if not pid: return
    if pid in demo_active:
        asyncio.create_task(demo_auto_reply(pid, uid))
        return
    await bot.send_message(pid, message.text)

# ---------------- ADMIN ----------------
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Not admin")
        return
    await message.answer("✅ Admin Panel")

# ---------------- RUN ----------------
async def main():
    await connect_db()
    await init_db()
    await load_demo_users()
    print("💜 Minglo Bot Running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
