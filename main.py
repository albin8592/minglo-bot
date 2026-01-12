import os
import asyncio
import asyncpg
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------- CONFIG ----------------
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL URL

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- DATABASE ----------------
db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge_type TEXT DEFAULT NULL,
            banned BOOLEAN DEFAULT FALSE
        );
        """)

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("🔀 Random Chat (Free)")],
            [KeyboardButton("👧 Find Girls")],
            [KeyboardButton("👦 Find Boys")],
            [KeyboardButton("📢 Invite & Earn Premium")],
            [KeyboardButton("💎 VIP Status")],
            [KeyboardButton("⏭ Next"), KeyboardButton("❌ Stop")],
            [KeyboardButton("🚫 Block & Report"), KeyboardButton("✅ Unblock")]
        ],
        resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton("👦 Boy"), KeyboardButton("👧 Girl")]],
        resize_keyboard=True
    )

# ---------------- IN-MEMORY ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
blocked = {}
skips = {}
admin_state = {}
FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

# ---------------- HELPERS ----------------
async def get_user(uid):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1;", uid)
        return user

async def create_user(uid):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING;", uid)

async def update_user(uid, **kwargs):
    if not kwargs:
        return
    set_clause = ", ".join([f"{k}=${i+2}" for i, k in enumerate(kwargs.keys())])
    values = list(kwargs.values())
    async with db_pool.acquire() as conn:
        await conn.execute(f"UPDATE users SET {set_clause} WHERE id=$1;", uid, *values)

async def check_banned(message):
    uid = message.from_user.id
    user = await get_user(uid)
    if user and user["banned"]:
        await message.answer("🚫 You are banned. Contact admin to unban.")
        return True
    return False

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    if await check_banned(message): return

    await create_user(uid)
    user = await get_user(uid)

    args = message.text.split()
    # Referral handling
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
        if ref_id != uid:
            ref = await get_user(ref_id)
            if ref:
                new_referrals = ref["referrals"] + 1
                badge_type = "invite" if new_referrals >= PREMIUM_REFERRALS else ref["badge_type"]
                premium = True if new_referrals >= PREMIUM_REFERRALS else ref["premium"]
                await update_user(ref_id, referrals=new_referrals, badge_type=badge_type, premium=premium)
                try:
                    await bot.send_message(ref_id, f"🎉 New referral!\n👥 {new_referrals}/{PREMIUM_REFERRALS} referrals")
                except: pass

    # Profile setup
    if not user["name"]:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE SETUP ----------------
@dp.message(lambda m: not m.from_user.id in active_chats)
async def profile_setup(message: types.Message):
    uid = message.from_user.id
    if await check_banned(message): return
    user = await get_user(uid)
    if not user: return

    # Name
    if not user["name"]:
        await update_user(uid, name=message.text)
        await message.answer("🎂 Age (18+)?")
        return
    # Age
    if not user["age"]:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await update_user(uid, age=int(message.text))
        await message.answer("📍 Place?")
        return
    # Place
    if not user["place"]:
        await update_user(uid, place=message.text)
        await message.answer("Select gender:", reply_markup=gender_keyboard())
        return
    # Gender
    if not user["gender"]:
        if message.text not in ["👦 Boy", "👧 Girl"]:
            return
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed!", reply_markup=main_keyboard())
        return

# ---------------- VIP STATUS ----------------
@dp.message(lambda m: m.text == "💎 VIP Status")
async def vip_status(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    user = await get_user(uid)
    if not user: return
    status = user["badge_type"] or "None"
    await message.answer(f"💎 VIP Status: {status}\n👥 Referrals: {user['referrals']}/{PREMIUM_REFERRALS}")

# ---------------- ADMIN PANEL ----------------
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Not admin")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban")],
        [InlineKeyboardButton("✅ Unban User", callback_data="admin_unban")],
        [InlineKeyboardButton("👑 Give VIP", callback_data="admin_premium")],
        [InlineKeyboardButton("👥 View Users", callback_data="admin_users")],
        [InlineKeyboardButton("❌ Close Panel", callback_data="admin_close")]
    ])
    await message.answer("✅ Admin Panel", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data and c.data.startswith("admin_"))
async def admin_callbacks(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Not allowed", show_alert=True)
        return
    data = callback.data
    await callback.answer()
    if data == "admin_users":
        users = await db_pool.fetch("SELECT * FROM users;")
        if not users:
            await callback.message.edit_text("❌ No users yet")
            return
        text = ""
        for u in users:
            text += f"ID: {u['id']}, Name: {u['name']}, Age: {u['age']}, Gender: {u['gender']}, Premium: {u['premium']}, Banned: {u['banned']}\n"
        await callback.message.edit_text(text)
    elif data == "admin_broadcast":
        admin_state[ADMIN_ID] = "broadcast"
        await callback.message.answer("📢 Send message to broadcast:")
    elif data == "admin_ban":
        admin_state[ADMIN_ID] = "ban"
        await callback.message.answer("🚫 Send USER ID to ban:")
    elif data == "admin_unban":
        admin_state[ADMIN_ID] = "unban"
        await callback.message.answer("✅ Send USER ID to unban:")
    elif data == "admin_premium":
        admin_state[ADMIN_ID] = "premium"
        await callback.message.answer("👑 Send USER ID to give VIP:")
    elif data == "admin_close":
        admin_state.pop(ADMIN_ID, None)
        await callback.message.edit_text("❌ Admin Panel closed")

@dp.message(lambda m: m.from_user.id == ADMIN_ID and ADMIN_ID in admin_state)
async def admin_actions(message: types.Message):
    action = admin_state[ADMIN_ID]
    if action == "broadcast":
        users = await db_pool.fetch("SELECT id FROM users WHERE banned=FALSE;")
        for u in users:
            try:
                await message.copy_to(u["id"])
            except: pass
        await message.answer("✅ Broadcast completed")
    elif action in ["ban", "unban", "premium"]:
        try:
            uid = int(message.text)
            user = await get_user(uid)
            if not user:
                await message.answer("❌ User not found")
                return
            if action == "ban":
                await update_user(uid, banned=True)
                await message.answer(f"🚫 User {uid} banned")
            elif action == "unban":
                await update_user(uid, banned=False)
                await message.answer(f"✅ User {uid} unbanned")
            elif action == "premium":
                await update_user(uid, premium=True, badge_type="admin")
                await message.answer(f"👑 User {uid} granted VIP")
        except:
            await message.answer("❌ Invalid ID")
    admin_state.pop(ADMIN_ID, None)

# ---------------- RUN BOT ----------------
async def main():
    await init_db()
    print("💜 Minglo Bot Running with PostgreSQL...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
