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

bot = Bot(API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- RUNTIME (MEMORY) ----------------
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
    "Where are you from?", "🙂", "Haha 😄"
]

# ---------------- DB ----------------
db = None

async def connect_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    async with db.acquire() as c:
        await c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge_type TEXT
        );
        CREATE TABLE IF NOT EXISTS banned_users(user_id BIGINT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS blocked_users(user_id BIGINT, blocked_id BIGINT);
        """)

async def get_user(uid):
    async with db.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid):
    async with db.acquire() as c:
        await c.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid
        )

async def update_user(uid, **k):
    if not k: return
    keys = list(k.keys())
    vals = list(k.values())
    sets = ", ".join(f"{x}=${i+1}" for i,x in enumerate(keys))
    async with db.acquire() as c:
        await c.execute(
            f"UPDATE users SET {sets} WHERE user_id=${len(vals)+1}",
            *vals, uid
        )

async def is_banned(uid):
    async with db.acquire() as c:
        return await c.fetchval(
            "SELECT 1 FROM banned_users WHERE user_id=$1", uid
        ) is not None

async def block_user_db(uid, target):
    async with db.acquire() as c:
        await c.execute(
            "INSERT INTO blocked_users VALUES($1,$2)", uid, target
        )

async def is_blocked(uid, target):
    async with db.acquire() as c:
        return await c.fetchval(
            "SELECT 1 FROM blocked_users WHERE user_id=$1 AND blocked_id=$2",
            uid, target
        ) is not None

# ---------------- LOAD DEMO USERS ----------------
async def load_demo_users():
    async with db.acquire() as c:
        for uid, d in DEMO_USERS.items():
            await c.execute("""
            INSERT INTO users
            (user_id,name,age,place,gender,premium)
            VALUES ($1,$2,$3,$4,$5,true)
            ON CONFLICT (user_id) DO NOTHING
            """, uid, d["name"], d["age"], d["place"], d["gender"])
            demo_active.add(uid)

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton("🔀 Random Chat (Free)")],
        [KeyboardButton("👧 Find Girls"), KeyboardButton("👦 Find Boys")],
        [KeyboardButton("📢 Invite & Earn Premium")],
        [KeyboardButton("💎 VIP Status")],
        [KeyboardButton("⏭ Next"), KeyboardButton("❌ Stop")],
        [KeyboardButton("🚫 Block & Report")]
    ])

def gender_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton("👦 Boy"), KeyboardButton("👧 Girl")]
    ])

# ---------------- COMMON ----------------
async def check_banned(message):
    if await is_banned(message.from_user.id):
        await message.answer("🚫 You are banned")
        return True
    return False

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    args = message.text.split()

    await create_user(uid)
    skips.setdefault(uid, 0)

    if len(args) > 1 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            async with db.acquire() as c:
                if await c.fetchrow("SELECT 1 FROM users WHERE user_id=$1", ref):
                    await c.execute(
                        "UPDATE users SET referrals=referrals+1 WHERE user_id=$1", ref
                    )
                    count = await c.fetchval(
                        "SELECT referrals FROM users WHERE user_id=$1", ref
                    )
                    if count >= PREMIUM_REFERRALS:
                        await c.execute(
                            "UPDATE users SET premium=true,badge_type='invite' WHERE user_id=$1",
                            ref
                        )

    user = await get_user(uid)
    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE FLOW ----------------
@dp.message()
async def profile(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user or await check_banned(message): return

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
        await message.answer("Select gender:", reply_markup=gender_keyboard())
        return

    if user["gender"] is None:
        if message.text not in ["👦 Boy", "👧 Girl"]: return
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed", reply_markup=main_keyboard())
        return

    # relay
    if uid in active_chats:
        pid = active_chats[uid]
        if pid in demo_active:
            asyncio.create_task(demo_auto_reply(pid, uid))
            return
        await bot.send_message(pid, message.text)

# ---------------- MATCH ----------------
def mask_name(n): return n[0] + "***" if n else "User"

async def demo_auto_reply(demo, uid):
    await asyncio.sleep(random.randint(2,5))
    await bot.send_message(uid, random.choice(DEMO_REPLIES))

async def match_user(uid, pool, gender, message, allow_demo=True):
    await message.answer("🔎 Searching...")
    for p in list(pool):
        if p == uid or p in active_chats: continue
        if gender and (await get_user(p))["gender"] != gender: continue
        if await is_blocked(p, uid): continue

        pool.remove(p)
        active_chats[uid] = p
        active_chats[p] = uid

        u, v = await get_user(uid), await get_user(p)
        await message.answer(f"🎉 Match found!\nName: {mask_name(v['name'])}", reply_markup=main_keyboard())
        await bot.send_message(p, f"🎉 Match found!\nName: {mask_name(u['name'])}", reply_markup=main_keyboard())
        return

    if allow_demo:
        d = random.choice(list(demo_active))
        active_chats[uid] = d
        await message.answer(
            f"🎉 Match found!\nName: {mask_name((await get_user(d))['name'])}",
            reply_markup=main_keyboard()
        )
        asyncio.create_task(demo_auto_reply(d, uid))
        return

    pool.add(uid)
    await message.answer("⏳ Waiting...")

# ---------------- CHAT BUTTONS ----------------
@dp.message(lambda m: m.text == "🔀 Random Chat (Free)")
async def random_chat(m): await match_user(m.from_user.id, waiting_random, None, m)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def girls_chat(m):
    if not (await get_user(m.from_user.id))["premium"]:
        await m.answer("💎 Premium required")
        return
    await match_user(m.from_user.id, waiting_girls, "👧 Girl", m, False)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def boys_chat(m):
    if not (await get_user(m.from_user.id))["premium"]:
        await m.answer("💎 Premium required")
        return
    await match_user(m.from_user.id, waiting_boys, "👦 Boy", m, False)

# ---------------- NEXT / STOP ----------------
@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(m):
    uid = m.from_user.id
    if uid not in active_chats: return
    skips[uid] += 1
    if skips[uid] > FREE_SKIP_LIMIT and not (await get_user(uid))["premium"]:
        await m.answer("💎 Skip limit reached")
        return
    pid = active_chats.pop(uid)
    active_chats.pop(pid, None)
    await random_chat(m)

@dp.message(lambda m: m.text == "❌ Stop")
async def stop(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Chat ended")
    await m.answer("Stopped", reply_markup=main_keyboard())

# ---------------- BLOCK ----------------
@dp.message(lambda m: m.text == "🚫 Block & Report")
async def block(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await block_user_db(uid, pid)
        await bot.send_message(pid, "🚫 You were blocked")
    await m.answer("🚫 User blocked")

# ---------------- VIP / INVITE ----------------
@dp.message(lambda m: m.text == "💎 VIP Status")
async def vip(m):
    u = await get_user(m.from_user.id)
    await m.answer(
        "👑 VIP" if u["premium"]
        else f"❌ Not VIP\nReferrals {u['referrals']}/{PREMIUM_REFERRALS}"
    )

@dp.message(lambda m: m.text == "📢 Invite & Earn Premium")
async def invite(m):
    link = f"https://t.me/{(await bot.me()).username}?start={m.from_user.id}"
    await m.answer(f"🔗 Invite link:\n{link}")

# ---------------- RUN ----------------
async def main():
    await connect_db()
    await init_db()
    await load_demo_users()
    print("💜 Minglo Bot Running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
