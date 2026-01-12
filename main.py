import os
import asyncio
import random
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# ✅ Bot token & admin ID
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")  # e.g., postgres://user:pass@localhost:5432/dbname

# ---------------- INIT ----------------
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------- DATABASE ----------------
async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge_type TEXT DEFAULT NULL
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS blocked (
            user_id BIGINT,
            blocked_id BIGINT,
            PRIMARY KEY(user_id, blocked_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS skips (
            user_id BIGINT PRIMARY KEY,
            remaining INT DEFAULT 5
        )
    """)
    await conn.close()

async def get_user(uid):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
    await conn.close()
    return dict(row) if row else None

async def create_user(uid):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("INSERT INTO users(id) VALUES($1) ON CONFLICT DO NOTHING", uid)
    await conn.close()

async def update_user(uid, **kwargs):
    if not kwargs: return
    conn = await asyncpg.connect(DATABASE_URL)
    query = "UPDATE users SET " + ", ".join([f"{k}=${i+2}" for i, k in enumerate(kwargs)]) + " WHERE id=$1"
    await conn.execute(query, uid, *kwargs.values())
    await conn.close()

async def block_user(uid, target_id):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("INSERT INTO blocked(user_id, blocked_id) VALUES($1,$2) ON CONFLICT DO NOTHING", uid, target_id)
    await conn.close()

async def unblock_user(uid, target_id):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("DELETE FROM blocked WHERE user_id=$1 AND blocked_id=$2", uid, target_id)
    await conn.close()

async def is_blocked(uid, target_id):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT 1 FROM blocked WHERE user_id=$1 AND blocked_id=$2", uid, target_id)
    await conn.close()
    return bool(row)

async def get_skips(uid):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT remaining FROM skips WHERE user_id=$1", uid)
    if not row:
        await conn.execute("INSERT INTO skips(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
        row = {'remaining': 5}
    await conn.close()
    return row['remaining']

async def use_skip(uid):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT remaining FROM skips WHERE user_id=$1", uid)
    remaining = 5 if not row else row['remaining']
    if remaining > 0:
        await conn.execute("UPDATE skips SET remaining=$1 WHERE user_id=$2", remaining-1, uid)
    await conn.close()
    return remaining > 0
# ---------------- ADMIN PANEL ----------------
def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 View Users", callback_data="admin_view_users")],
        [InlineKeyboardButton(text="🚫 Ban User", callback_data="admin_ban_user")],
        [InlineKeyboardButton(text="✅ Unban User", callback_data="admin_unban_user")],
        [InlineKeyboardButton(text="💎 Promote VIP", callback_data="admin_vip_user")],
        [InlineKeyboardButton(text="🔻 Demote VIP", callback_data="admin_remove_vip")],
        [InlineKeyboardButton(text="❌ Delete User", callback_data="admin_delete_user")]
    ])

# ---------------- ADMIN COMMAND ----------------
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ You are not an admin.")
        return
    await message.answer("⚙️ Admin Panel", reply_markup=admin_keyboard())

# ---------------- ADMIN CALLBACKS ----------------
@dp.callback_query(lambda c: c.data.startswith("admin_"))
async def admin_actions(callback: types.CallbackQuery):
    action = callback.data
    await callback.answer()  # avoid loading animation
    
    if action == "admin_view_users":
        conn = await asyncpg.connect(DATABASE_URL)
        rows = await conn.fetch("SELECT id, name, age, gender, premium, referrals FROM users ORDER BY id DESC LIMIT 20")
        await conn.close()
        if not rows:
            await callback.message.edit_text("❌ No users found.")
            return
        msg = "👥 Last 20 Users:\n\n" + "\n".join(
            [f"ID: {r['id']}\nName: {r['name']}\nAge: {r['age']}\nGender: {r['gender']}\nPremium: {r['premium']}\nReferrals: {r['referrals']}\n---" for r in rows]
        )
        await callback.message.edit_text(msg, reply_markup=admin_keyboard())

    elif action in ["admin_ban_user", "admin_unban_user", "admin_vip_user", "admin_remove_vip", "admin_delete_user"]:
        await callback.message.answer("⚠️ Send the user ID for this action:")

        @dp.message(lambda m: m.text.isdigit() and m.from_user.id == ADMIN_ID)
        async def process_user_id(msg: types.Message):
            target_id = int(msg.text)
            if action == "admin_ban_user":
                banned_users.add(target_id)
                await msg.answer(f"🚫 User {target_id} banned.")
            elif action == "admin_unban_user":
                banned_users.discard(target_id)
                await msg.answer(f"✅ User {target_id} unbanned.")
            elif action == "admin_vip_user":
                await update_user(target_id, premium=True)
                await msg.answer(f"💎 User {target_id} promoted to VIP.")
            elif action == "admin_remove_vip":
                await update_user(target_id, premium=False)
                await msg.answer(f"🔻 User {target_id} VIP removed.")
            elif action == "admin_delete_user":
                conn = await asyncpg.connect(DATABASE_URL)
                await conn.execute("DELETE FROM users WHERE id=$1", target_id)
                await conn.execute("DELETE FROM blocked WHERE user_id=$1 OR blocked_id=$1", target_id)
                await conn.close()
                active_chats.pop(target_id, None)
                await msg.answer(f"❌ User {target_id} deleted.")
            await msg.answer("⚙️ Admin Panel", reply_markup=admin_keyboard())

# ---------------- IN-MEMORY CHAT STATE ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
banned_users = set()

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔀 Random Chat (Free)")],
            [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
            [KeyboardButton(text="📢 Invite & Earn Premium"), KeyboardButton(text="💎 VIP Status")],
            [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
            [KeyboardButton(text="🚫 Block & Report"), KeyboardButton(text="✅ Unblock")]
        ], resize_keyboard=True
    )

def gender_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]], resize_keyboard=True
    )

# ---------------- START COMMAND ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    uid = message.from_user.id
    await create_user(uid)
    user = await get_user(uid)
    if not user['name']:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE SETUP ----------------
@dp.message(lambda m: True)
async def profile_setup(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    if not user: return

    if not user['name']:
        await update_user(uid, name=message.text)
        await message.answer("🎂 Age (18+)?")
        return
    if not user['age']:
        if not message.text.isdigit() or int(message.text) < 18:
            await message.answer("❌ 18+ only")
            return
        await update_user(uid, age=int(message.text))
        await message.answer("📍 Place?")
        return
    if not user['place']:
        await update_user(uid, place=message.text)
        await message.answer("Select your gender:", reply_markup=gender_keyboard())
        return
    if not user['gender']:
        if message.text not in ["👦 Boy", "👧 Girl"]: return
        await update_user(uid, gender=message.text)
        await message.answer("✅ Profile completed!", reply_markup=main_keyboard())

# ---------------- MATCH ENGINE ----------------
def mask_name(name: str) -> str:
    if not name: return "User"
    return name[0] + "***" + name[-1] if len(name) > 2 else name[0] + "*"

async def match_user(uid, pool, target_gender, message):
    if uid in banned_users: return
    searching_msg = await message.answer("🔎 Searching...")
    candidates = [user for user in pool if user != uid and user not in active_chats]
    if target_gender:
        candidates = [u for u in candidates if (await get_user(u))['gender']==target_gender]
    
    if candidates:
        partner = candidates[0]
        pool.discard(partner)
        active_chats[uid] = partner
        active_chats[partner] = uid
        await searching_msg.delete()
        partner_info = await get_user(partner)
        user_info = await get_user(uid)
        await bot.send_message(uid, f"🎉 Match found!\nName: {mask_name(partner_info['name'])}\nAge: {partner_info['age']}\nPlace: {partner_info['place']}", reply_markup=main_keyboard())
        await bot.send_message(partner, f"🎉 Match found!\nName: {mask_name(user_info['name'])}\nAge: {user_info['age']}\nPlace: {user_info['place']}", reply_markup=main_keyboard())
    else:
        pool.add(uid)
        await searching_msg.edit_text("⏳ Waiting for a partner...")

# ---------------- CHAT COMMANDS ----------------
@dp.message(lambda m: m.text in ["🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys"])
async def start_chat(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        await message.answer("❌ Already in a chat! Use ❌ Stop first.")
        return
    if message.text == "🔀 Random Chat (Free)":
        await match_user(uid, waiting_random, None, message)
    elif message.text in ["👧 Find Girls", "👦 Find Boys"]:
        user = await get_user(uid)
        if not user['premium']:
            await message.answer(f"💎 Premium Required\nInvite {PREMIUM_REFERRALS} friends to unlock.")
            return
        pool = waiting_girls if message.text=="👧 Find Girls" else waiting_boys
        gender = "👧 Girl" if message.text=="👧 Find Girls" else "👦 Boy"
        await match_user(uid, pool, gender, message)

# ---------------- NEXT / STOP ----------------
@dp.message(lambda m: m.text == "❌ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    partner = active_chats.pop(uid, None)
    if partner:
        active_chats.pop(partner, None)
        await bot.send_message(partner, "❌ Chat stopped by partner.", reply_markup=main_keyboard())
    await message.answer("❌ Chat stopped.", reply_markup=main_keyboard())

@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(message: types.Message):
    uid = message.from_user.id
    partner = active_chats.pop(uid, None)
    if partner:
        active_chats.pop(partner, None)
        await bot.send_message(partner, "❌ Chat stopped by partner.", reply_markup=main_keyboard())
    if await use_skip(uid):
        await message.answer("⏭ Skipping to next chat...")
        await start_chat(message)
    else:
        await message.answer("❌ No skips remaining. Invite friends to earn more.")

# ---------------- BLOCK / UNBLOCK ----------------
@dp.message(lambda m: m.text == "🚫 Block & Report")
async def block_command(message: types.Message):
    uid = message.from_user.id
    partner = active_chats.get(uid)
    if partner:
        await block_user(uid, partner)
        await stop_chat(message)
        await message.answer("🚫 User blocked and chat ended.", reply_markup=main_keyboard())
    else:
        await message.answer("❌ You are not in a chat.", reply_markup=main_keyboard())

@dp.message(lambda m: m.text == "✅ Unblock")
async def unblock_command(message: types.Message):
    uid = message.from_user.id
    # For simplicity, unblock last blocked user
    await message.answer("✅ To unblock, type the user ID:")
    @dp.message(lambda m: m.text.isdigit())
    async def do_unblock(msg: types.Message):
        target_id = int(msg.text)
        await unblock_user(uid, target_id)
        await msg.answer(f"✅ User {target_id} unblocked.", reply_markup=main_keyboard())

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    uid = message.from_user.id
    pid = active_chats.get(uid)
    if not pid: return
    if await is_blocked(pid, uid):
        await message.answer("❌ You are blocked by this user.")
        return
    await bot.send_message(pid, message.text)

# ---------------- RUN BOT ----------------
async def main():
    await init_db()
    print("💜 Bot Running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
