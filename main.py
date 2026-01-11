import os
import asyncio
import random
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
db: asyncpg.Pool = None

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

# ================= DB INIT =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    async with db.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge TEXT
        );

        CREATE TABLE IF NOT EXISTS waiting (
            user_id BIGINT,
            mode TEXT
        );

        CREATE TABLE IF NOT EXISTS active_chats (
            u1 BIGINT,
            u2 BIGINT
        );

        CREATE TABLE IF NOT EXISTS blocks (
            user_id BIGINT,
            blocked_id BIGINT
        );

        CREATE TABLE IF NOT EXISTS bans (
            user_id BIGINT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS skips (
            user_id BIGINT PRIMARY KEY,
            count INT DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS referrals (
            referrer BIGINT,
            joined BIGINT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS demo_users (
            id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT
        );
        """)

# ================= DEMO USERS =================
DEMO_USERS = [
    (10001,"Anu",21,"Kochi","👧 Girl"),
    (10002,"Meera",22,"Calicut","👧 Girl"),
    (20001,"Rahul",23,"Kochi","👦 Boy"),
    (20002,"Arjun",24,"Calicut","👦 Boy")
]

async def load_demo_users():
    async with db.acquire() as con:
        for d in DEMO_USERS:
            await con.execute("""
            INSERT INTO demo_users(id,name,age,place,gender)
            VALUES($1,$2,$3,$4,$5)
            ON CONFLICT DO NOTHING
            """, *d)

# ================= KEYBOARDS =================
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

# ================= HELPERS =================
async def is_banned(uid):
    async with db.acquire() as con:
        return await con.fetchval("SELECT 1 FROM bans WHERE user_id=$1", uid)

def mask_name(name):
    if not name: return "User"
    return name[0] + "***"

# ================= START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    args = message.text.split()

    if await is_banned(uid):
        await message.answer("🚫 You are banned")
        return

    async with db.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        if not user:
            await con.execute("INSERT INTO users(id) VALUES($1)", uid)
            await con.execute("INSERT INTO skips(user_id) VALUES($1)", uid)

            if len(args) > 1 and args[1].isdigit():
                ref = int(args[1])
                if ref != uid:
                    try:
                        await con.execute(
                            "INSERT INTO referrals(referrer,joined) VALUES($1,$2)",
                            ref, uid
                        )
                        await con.execute(
                            "UPDATE users SET referrals=referrals+1 WHERE id=$1",
                            ref
                        )
                        count = await con.fetchval(
                            "SELECT referrals FROM users WHERE id=$1", ref
                        )
                        if count >= PREMIUM_REFERRALS:
                            await con.execute(
                                "UPDATE users SET premium=TRUE,badge='invite' WHERE id=$1",
                                ref
                            )
                            await bot.send_message(ref, "🎉 Premium unlocked!")
                    except:
                        pass

            await message.answer("👤 Your name?")
            return

        if not user["name"]:
            await message.answer("👤 Your name?")
        else:
            await message.answer("💜 Welcome back", reply_markup=main_keyboard())

# ================= PROFILE =================
@dp.message(lambda m: m.text and m.from_user.id)
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    async with db.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        if user["name"] is None:
            await con.execute("UPDATE users SET name=$1 WHERE id=$2", message.text, uid)
            await message.answer("🎂 Age?")
        elif user["age"] is None:
            if not message.text.isdigit() or int(message.text) < 18:
                await message.answer("18+ only")
                return
            await con.execute("UPDATE users SET age=$1 WHERE id=$2", int(message.text), uid)
            await message.answer("📍 Place?")
        elif user["place"] is None:
            await con.execute("UPDATE users SET place=$1 WHERE id=$2", message.text, uid)
            await message.answer("Select gender", reply_markup=gender_keyboard())
        elif user["gender"] is None and message.text in ["👦 Boy","👧 Girl"]:
            await con.execute("UPDATE users SET gender=$1 WHERE id=$2", message.text, uid)
            await message.answer("✅ Profile completed", reply_markup=main_keyboard())

# ================= MATCHING =================
async def match_user(uid, mode, gender, message):
    async with db.acquire() as con:
        await con.execute("DELETE FROM waiting WHERE user_id=$1", uid)

        partner = await con.fetchrow("""
        SELECT u.id,u.name,u.age,u.place
        FROM waiting w
        JOIN users u ON u.id=w.user_id
        WHERE w.mode=$1 AND u.gender=$2 AND u.id!=$3
        LIMIT 1
        """, mode, gender, uid)

        if partner:
            await con.execute("DELETE FROM waiting WHERE user_id=$1", partner["id"])
            await con.execute(
                "INSERT INTO active_chats VALUES($1,$2)",
                uid, partner["id"]
            )

            await bot.send_message(
                partner["id"],
                f"🎉 Match!\n{mask_name(partner['name'])}",
                reply_markup=main_keyboard()
            )
            await message.answer(
                f"🎉 Match!\n{mask_name(partner['name'])}",
                reply_markup=main_keyboard()
            )
            return

        await con.execute("INSERT INTO waiting VALUES($1,$2)", uid, mode)
        await message.answer("⏳ Waiting for partner...")

# ================= CHAT BUTTONS =================
@dp.message(F.text.in_(["🔀 Random Chat (Free)","👧 Find Girls","👦 Find Boys"]))
async def start_chat(message: types.Message):
    uid = message.from_user.id
    async with db.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1", uid)

    if message.text == "🔀 Random Chat (Free)":
        await match_user(uid,"random",None,message)

    elif message.text == "👧 Find Girls":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        await match_user(uid,"girls","👧 Girl",message)

    elif message.text == "👦 Find Boys":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        await match_user(uid,"boys","👦 Boy",message)

# ================= RELAY =================
@dp.message(lambda m: not m.text.startswith("/"))
async def relay(message: types.Message):
    uid = message.from_user.id
    async with db.acquire() as con:
        chat = await con.fetchrow(
            "SELECT * FROM active_chats WHERE u1=$1 OR u2=$1", uid
        )
    if not chat:
        return
    pid = chat["u2"] if chat["u1"] == uid else chat["u1"]
    await bot.send_message(pid, message.text)

# ================= ADMIN =================
@dp.message(Command("ban"))
async def ban(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    uid = int(message.text.split()[1])
    async with db.acquire() as con:
        await con.execute("INSERT INTO bans VALUES($1) ON CONFLICT DO NOTHING", uid)
    await message.answer("🚫 User banned")

@dp.message(Command("givepremium"))
async def givepremium(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    uid = int(message.text.split()[1])
    async with db.acquire() as con:
        await con.execute(
            "UPDATE users SET premium=TRUE,badge='admin' WHERE id=$1", uid
        )
    await message.answer("👑 Premium granted")

# ================= RUN =================
async def main():
    await init_db()
    await load_demo_users()
    print("✅ Minglo DB Bot Running")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
