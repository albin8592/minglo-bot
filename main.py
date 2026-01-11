import os, asyncio, asyncpg
from datetime import date
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
BOT_USERNAME = "minglochat_bot"

DAILY_LIMIT = 100
PREMIUM_REFERRALS = 100

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db: asyncpg.Pool = None

waiting_random, waiting_girls, waiting_boys = set(), set(), set()
active_chats = {}

# ================= DB INIT =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id BIGINT PRIMARY KEY,
        name TEXT, age INT, place TEXT, gender TEXT,
        premium BOOLEAN DEFAULT FALSE,
        referrals INT DEFAULT 0
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS referrals(
        referrer BIGINT,
        referred BIGINT UNIQUE
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS daily(
        user_id BIGINT,
        day DATE,
        count INT,
        PRIMARY KEY(user_id, day)
    );
    """)

    await db.execute("CREATE TABLE IF NOT EXISTS bans(user_id BIGINT PRIMARY KEY);")
    await db.execute("CREATE TABLE IF NOT EXISTS blocks(user_id BIGINT, blocked BIGINT);")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS reports(
        reporter BIGINT,
        reported BIGINT,
        time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

# ================= UI =================
def main_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="🔀 Random Chat")],
        [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
        [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
        [KeyboardButton(text="📢 Invite & Earn")],
        [KeyboardButton(text="💎 VIP Status")]
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

async def daily_limit(uid):
    user = await get_user(uid)
    if user["premium"]:
        return True
    today = date.today()
    row = await db.fetchrow("SELECT count FROM daily WHERE user_id=$1 AND day=$2", uid, today)
    if row and row["count"] >= DAILY_LIMIT:
        return False
    if row:
        await db.execute("UPDATE daily SET count=count+1 WHERE user_id=$1 AND day=$2", uid, today)
    else:
        await db.execute("INSERT INTO daily VALUES($1,$2,1)", uid, today)
    return True

# ================= START =================
@dp.message(Command("start"))
async def start(m: types.Message):
    uid = m.from_user.id
    if await is_banned(uid):
        await m.answer("🚫 You are banned")
        return

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
                        await db.execute("UPDATE users SET premium=TRUE WHERE user_id=$1", ref)
                        await bot.send_message(ref, "🎉 You are now PREMIUM!")

    user = await get_user(uid)
    if not user["name"]:
        await m.answer("👤 Enter your name")
    else:
        await m.answer("💜 Welcome to Minglo Chat", reply_markup=main_kb())

# ================= PROFILE =================
@dp.message()
async def profile(m: types.Message):
    uid = m.from_user.id
    user = await get_user(uid)
    if not user or user["gender"]:
        return

    if not user["name"]:
        await db.execute("UPDATE users SET name=$1 WHERE user_id=$2", m.text, uid)
        await m.answer("🎂 Enter age (18+)")
        return

    if not user["age"]:
        if not m.text.isdigit() or int(m.text) < 18:
            await m.answer("❌ 18+ only")
            return
        await db.execute("UPDATE users SET age=$1 WHERE user_id=$2", int(m.text), uid)
        await m.answer("📍 Your place?")
        return

    if not user["place"]:
        await db.execute("UPDATE users SET place=$1 WHERE user_id=$2", m.text, uid)
        await m.answer("Select gender", reply_markup=gender_kb())
        return

    if m.text in ["👦 Boy", "👧 Girl"]:
        await db.execute("UPDATE users SET gender=$1 WHERE user_id=$2", m.text, uid)
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        await m.answer(
            f"✨ Profile Completed!\n\n"
            f"🎁 Invite 100 friends → Premium\n\n"
            f"🔗 Your link:\n{link}",
            reply_markup=main_kb()
        )

# ================= MATCH =================
async def match(uid, pool, m):
    if uid in active_chats:
        await m.answer("❌ Already chatting")
        return

    if not await daily_limit(uid):
        await m.answer("⛔ Daily limit reached (100/day)")
        return

    wait = await m.answer("🔎 Searching...")
    for u in list(pool):
        if u != uid and u not in active_chats:
            pool.remove(u)
            active_chats[uid] = u
            active_chats[u] = uid
            await wait.delete()
            await bot.send_message(u, "🎉 Match found")
            await m.answer("🎉 Match found")
            return

    pool.add(uid)
    await wait.edit_text("⏳ Waiting for partner...")

# ================= BUTTONS =================
@dp.message(lambda m: m.text == "🔀 Random Chat")
async def random(m): await match(m.from_user.id, waiting_random, m)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def girls(m):
    if not (await get_user(m.from_user.id))["premium"]:
        await m.answer("💎 Premium required")
        return
    await match(m.from_user.id, waiting_girls, m)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def boys(m):
    if not (await get_user(m.from_user.id))["premium"]:
        await m.answer("💎 Premium required")
        return
    await match(m.from_user.id, waiting_boys, m)

@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "⏭ Partner skipped")
    await match(uid, waiting_random, m)

@dp.message(lambda m: m.text == "❌ Stop")
async def stop(m):
    uid = m.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Chat ended")
    await m.answer("✅ Stopped", reply_markup=main_kb())

@dp.message(lambda m: m.text == "📢 Invite & Earn")
async def invite(m):
    uid = m.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start={uid}"
    await m.answer(f"🎁 Invite 100 users → Premium\n\n🔗 {link}")

@dp.message(lambda m: m.text == "💎 VIP Status")
async def vip(m):
    user = await get_user(m.from_user.id)
    await m.answer(
        f"💎 VIP STATUS\n\n"
        f"Premium: {'YES' if user['premium'] else 'NO'}\n"
        f"Referrals: {user['referrals']}/100"
    )

# ================= ADMIN =================
@dp.message(Command("admin"))
async def admin(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    total = await db.fetchval("SELECT COUNT(*) FROM users")
    premium = await db.fetchval("SELECT COUNT(*) FROM users WHERE premium=TRUE")
    banned = await db.fetchval("SELECT COUNT(*) FROM bans")
    await m.answer(
        f"📊 ADMIN PANEL\n\n"
        f"👥 Users: {total}\n"
        f"💎 Premium: {premium}\n"
        f"🚫 Banned: {banned}"
    )

# ================= RUN =================
async def main():
    await init_db()
    print("🔥 Minglo Professional Bot Running")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
