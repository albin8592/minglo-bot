import os
import asyncio
import random
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage

# ================= CONFIG =================
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db: asyncpg.Pool = None

# ================= MEMORY ONLY =================
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
admin_state = {}
demo_active = set()
demo_reply_count = {}

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

# ================= DEMO USERS =================
DEMO_USERS = {
    10001: {"name": "Anu", "age": 21, "place": "Kochi", "gender": "👧 Girl"},
    10002: {"name": "Meera", "age": 22, "place": "Calicut", "gender": "👧 Girl"},
    20001: {"name": "Rahul", "age": 23, "place": "Kochi", "gender": "👦 Boy"},
    20002: {"name": "Arjun", "age": 24, "place": "Calicut", "gender": "👦 Boy"},
}

DEMO_REPLIES = ["Hi 🙂", "Hello!", "How are you?", "Nice 🙂"]

# ================= DB INIT =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    await db.execute("""
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
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS banned(
        user_id BIGINT PRIMARY KEY
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS blocked(
        user_id BIGINT,
        blocked_id BIGINT
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS skips(
        user_id BIGINT PRIMARY KEY,
        count INT DEFAULT 0
    );
    """)

# ================= HELPERS =================
async def is_banned(uid: int) -> bool:
    row = await db.fetchrow("SELECT 1 FROM banned WHERE user_id=$1", uid)
    return bool(row)

async def get_user(uid: int):
    return await db.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)

async def create_user(uid: int):
    await db.execute("""
    INSERT INTO users(user_id) VALUES($1)
    ON CONFLICT DO NOTHING
    """, uid)

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔀 Random Chat (Free)")],
            [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
            [KeyboardButton(text="📢 Invite & Earn Premium")],
            [KeyboardButton(text="💎 VIP Status")],
            [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
            [KeyboardButton(text="🚫 Block & Report")]
        ],
        resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]],
        resize_keyboard=True
    )

def mask_name(name):
    if not name: return "User"
    return name[0] + "***"

# ================= START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    if await is_banned(uid):
        await message.answer("🚫 You are banned")
        return

    await create_user(uid)
    user = await get_user(uid)

    if not user["name"]:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ================= PROFILE SETUP =================
@dp.message()
async def profile_flow(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user:
        return

    # ✅ PROFILE COMPLETE → IGNORE
    if user["gender"] is not None:
        return

    # NAME
    if user["name"] is None:
        await db.execute(
            "UPDATE users SET name=$1 WHERE user_id=$2",
            message.text, uid
        )
        await message.answer("🎂 Age?")
        return

    # AGE
    if user["age"] is None:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await db.execute(
            "UPDATE users SET age=$1 WHERE user_id=$2",
            int(message.text), uid
        )
        await message.answer("📍 Place?")
        return

    # PLACE
    if user["place"] is None:
        await db.execute(
            "UPDATE users SET place=$1 WHERE user_id=$2",
            message.text, uid
        )
        await message.answer(
            "Select gender",
            reply_markup=gender_keyboard()
        )
        return

    # GENDER
    if user["gender"] is None and message.text in ["👦 Boy", "👧 Girl"]:
        await db.execute(
            "UPDATE users SET gender=$1 WHERE user_id=$2",
            message.text, uid
        )
        await message.answer(
            "✅ Profile Completed",
            reply_markup=main_keyboard()
        )
        return


# ================= MATCH =================
async def match_user(uid, pool, gender, message):
    # already chatting?
    if uid in active_chats:
        await message.answer("❌ You are already in a chat")
        return

    searching = await message.answer("🔎 Searching...")

    # 🔹 TRY MATCH FROM WAITING POOL
    for waiting_uid in list(pool):
        if waiting_uid == uid:
            continue
        if waiting_uid in active_chats:
            pool.discard(waiting_uid)
            continue

        partner = await get_user(waiting_uid)
        me = await get_user(uid)

        if not partner or not me:
            pool.discard(waiting_uid)
            continue

        if gender and partner["gender"] != gender:
            continue

        # ✅ MATCH FOUND
        pool.discard(waiting_uid)
        active_chats[uid] = waiting_uid
        active_chats[waiting_uid] = uid
        await searching.delete()

        await bot.send_message(
            uid,
            f"🎉 Match found!\n"
            f"Name: {mask_name(partner['name'])}\n"
            f"Age: {partner['age']}\n"
            f"Place: {partner['place']}",
            reply_markup=main_keyboard()
        )

        await bot.send_message(
            waiting_uid,
            f"🎉 Match found!\n"
            f"Name: {mask_name(me['name'])}\n"
            f"Age: {me['age']}\n"
            f"Place: {me['place']}",
            reply_markup=main_keyboard()
        )
        return

    # 🔹 NO MATCH → WAIT
    pool.add(uid)
    await searching.edit_text("⏳ Waiting for a partner...")


# ================= CHAT BUTTONS =================
@dp.message(lambda m: m.text == "🔀 Random Chat (Free)")
async def random_chat(message: types.Message):
    await match_user(message.from_user.id, waiting_random, None, message)

@dp.message(lambda m: m.text == "👧 Find Girls")
async def find_girls(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_girls, "👧 Girl", message)

@dp.message(lambda m: m.text == "👦 Find Boys")
async def find_boys(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user["premium"]:
        await message.answer("💎 Premium required")
        return
    await match_user(message.from_user.id, waiting_boys, "👦 Boy", message)

# ================= RELAY =================
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    uid = message.from_user.id
    pid = active_chats.get(uid)
    if pid:
        await bot.send_message(pid, message.text)

# ================= RUN =================
def load_demo_users():
    for uid, d in DEMO_USERS.items():
        demo_active.add(uid)

async def main():
    await init_db()
    load_demo_users()
    print("💜 Minglo Bot Running (DB Version)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())



