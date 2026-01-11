import os, asyncio, random, asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PREMIUM_REFERRALS = 5
FREE_SKIP_LIMIT = 5

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

# ---------------- RUNTIME ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
skips = {}

# ---------------- DB ----------------
async def connect_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    async with db_pool.acquire() as c:
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
    async with db_pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid):
    async with db_pool.acquire() as c:
        await c.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid
        )

async def update_user(uid, **k):
    if not k: return
    keys = list(k.keys())
    vals = list(k.values())
    sets = ", ".join(f"{x}=${i+1}" for i,x in enumerate(keys))
    async with db_pool.acquire() as c:
        await c.execute(
            f"UPDATE users SET {sets} WHERE user_id=${len(vals)+1}",
            *vals, uid
        )

# ---------------- UI ----------------
def main_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton("🔀 Random Chat")],
        [KeyboardButton("👧 Find Girls"), KeyboardButton("👦 Find Boys")],
        [KeyboardButton("📢 Invite & Earn Premium")],
        [KeyboardButton("💎 VIP Status")],
        [KeyboardButton("⏭ Next"), KeyboardButton("❌ Stop")],
        [KeyboardButton("🚫 Block")]
    ])

def gender_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton("👦 Boy"), KeyboardButton("👧 Girl")]
    ])

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    args = message.text.split()
    await create_user(uid)
    skips.setdefault(uid, 0)

    # referral
    if len(args) > 1 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            async with db_pool.acquire() as c:
                if await c.fetchrow("SELECT 1 FROM users WHERE user_id=$1", ref):
                    await c.execute(
                        "UPDATE users SET referrals = referrals + 1 WHERE user_id=$1", ref
                    )
                    count = await c.fetchval(
                        "SELECT referrals FROM users WHERE user_id=$1", ref
                    )
                    if count >= PREMIUM_REFERRALS:
                        await c.execute(
                            "UPDATE users SET premium=true, badge_type='invite' WHERE user_id=$1",
                            ref
                        )

    user = await get_user(uid)
    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome!", reply_markup=main_kb())

# ---------------- PROFILE ----------------
@dp.message()
async def profile(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user: return

    if user["name"] is None:
        await update_user(uid, name=message.text)
        await message.answer("🎂 Age?")
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
        await message.answer("Gender?", reply_markup=gender_kb())
        return

    if user["gender"] is None:
        if message.text not in ["👦 Boy","👧 Girl"]: return
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed", reply_markup=main_kb())
        return

    # relay
    if uid in active_chats and message.text not in [
        "⏭ Next","❌ Stop","🚫 Block",
        "🔀 Random Chat","👧 Find Girls","👦 Find Boys",
        "📢 Invite & Earn Premium","💎 VIP Status"
    ]:
        await bot.send_message(active_chats[uid], message.text)

# ---------------- MATCH ----------------
async def match(uid, pool, gender, message):
    for p in list(pool):
        if p == uid or p in active_chats: continue
        u1, u2 = await get_user(uid), await get_user(p)
        if gender and u2["gender"] != gender: continue
        pool.remove(p)
        active_chats[uid] = p
        active_chats[p] = uid
        await message.answer("🎉 Connected!", reply_markup=main_kb())
        await bot.send_message(p, "🎉 Connected!", reply_markup=main_kb())
        return
    pool.add(uid)
    await message.answer("⏳ Waiting...")

@dp.message(lambda m: m.text == "🔀 Random Chat")
async def random_chat(m): await match(m.from_user.id, waiting_random, None, m)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def girls_chat(m):
    if not (await get_user(m.from_user.id))["premium"]:
        await m.answer("💎 Premium only")
        return
    await match(m.from_user.id, waiting_girls, "👧 Girl", m)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def boys_chat(m):
    if not (await get_user(m.from_user.id))["premium"]:
        await m.answer("💎 Premium only")
        return
    await match(m.from_user.id, waiting_boys, "👦 Boy", m)

# ---------------- NEXT / STOP ----------------
@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(m):
    uid = m.from_user.id
    if uid not in active_chats: return
    pid = active_chats.pop(uid)
    active_chats.pop(pid, None)
    skips[uid] += 1
    if skips[uid] > FREE_SKIP_LIMIT and not (await get_user(uid))["premium"]:
        await m.answer("❌ Skip limit reached")
        return
    await random_chat(m)

@dp.message(lambda m: m.text == "❌ Stop")
async def stop_chat(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Chat ended")
    await m.answer("Stopped", reply_markup=main_kb())

# ---------------- BLOCK ----------------
@dp.message(lambda m: m.text == "🚫 Block")
async def block(m):
    uid = m.from_user.id
    if uid not in active_chats: return
    pid = active_chats[uid]
    async with db_pool.acquire() as c:
        await c.execute(
            "INSERT INTO blocked_users VALUES($1,$2)", uid, pid
        )
    await stop_chat(m)

# ---------------- VIP / INVITE ----------------
@dp.message(lambda m: m.text == "📢 Invite & Earn Premium")
async def invite(m):
    link = f"https://t.me/{(await bot.me()).username}?start={m.from_user.id}"
    await m.answer(f"Invite link:\n{link}\n{PREMIUM_REFERRALS} = VIP")

@dp.message(lambda m: m.text == "💎 VIP Status")
async def vip(m):
    u = await get_user(m.from_user.id)
    await m.answer(
        "✅ VIP" if u["premium"]
        else f"❌ Not VIP\nReferrals {u['referrals']}/{PREMIUM_REFERRALS}"
    )

# ---------------- RUN ----------------
async def main():
    await connect_db()
    await init_db()
    print("💜 Bot Running")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
