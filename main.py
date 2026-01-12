import os, asyncio, random, asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

# ---------------- DATABASE ----------------
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            place TEXT,
            gender TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referrals INT DEFAULT 0,
            badge_type TEXT
        )""")
        await con.execute("""
        CREATE TABLE IF NOT EXISTS banned_users(
            user_id BIGINT PRIMARY KEY
        )""")
        await con.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            id SERIAL PRIMARY KEY,
            sender BIGINT,
            receiver BIGINT,
            text TEXT,
            time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

# ---------------- DB HELPERS ----------------
async def add_user(uid):
    async with db_pool.acquire() as con:
        await con.execute(
            "INSERT INTO users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid
        )

async def get_user(uid):
    async with db_pool.acquire() as con:
        row = await con.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        return dict(row) if row else None

async def update_user(uid, field, value):
    async with db_pool.acquire() as con:
        await con.execute(
            f"UPDATE users SET {field}=$1 WHERE user_id=$2", value, uid
        )

async def is_banned(uid):
    async with db_pool.acquire() as con:
        return await con.fetchval(
            "SELECT 1 FROM banned_users WHERE user_id=$1", uid
        ) is not None

async def ban_user(uid):
    async with db_pool.acquire() as con:
        await con.execute(
            "INSERT INTO banned_users VALUES($1) ON CONFLICT DO NOTHING", uid
        )

async def unban_user(uid):
    async with db_pool.acquire() as con:
        await con.execute(
            "DELETE FROM banned_users WHERE user_id=$1", uid
        )

async def log_message(sender, receiver, text):
    async with db_pool.acquire() as con:
        await con.execute(
            "INSERT INTO messages(sender,receiver,text) VALUES($1,$2,$3)",
            sender, receiver, text
        )


# ---------------- MEMORY ----------------
waiting_random = set()       # Random chat (boys + girls)
waiting_find_girls = set()  # Girls waiting (for Find Girls)
waiting_find_boys = set()   # Boys waiting (for Find Boys)

active_chats = {}
blocked = {}
admin_state = {}
user_mode = {}  # uid -> "random" | "girls" | "boys"
def remove_from_all_queues(uid):
    waiting_random.discard(uid)
    waiting_find_girls.discard(uid)
    waiting_find_boys.discard(uid)

# ---------------- KEYBOARDS ----------------
def main_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="🔀 Random Chat (Free)")],
        [KeyboardButton(text="👧 Find Girls"), KeyboardButton(text="👦 Find Boys")],
        [KeyboardButton(text="📢 Invite & Earn Premium")],
        [KeyboardButton(text="💎 VIP Status")],
        [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
        [KeyboardButton(text="🚫 Block & Report"), KeyboardButton(text="✅ Unblock")]
    ])

def gender_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="👦 Boy"), KeyboardButton(text="👧 Girl")]
    ])

# ---------------- UTIL ----------------
async def check_banned(message):
    if await is_banned(message.from_user.id):
        await message.answer("🚫 You are banned.")
        return True
    return False

def mask(name):
    return name[0] + "***" if name else "User"

# ---------------- START + REFERRAL ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    if await check_banned(message): return

    uid = message.from_user.id
    args = message.text.split()
    await add_user(uid)

    if len(args) > 1 and args[1].isdigit():
        ref = int(args[1])
        if ref != uid:
            async with db_pool.acquire() as con:
                await con.execute(
                    "UPDATE users SET referrals=referrals+1 WHERE user_id=$1", ref
                )
                cnt = await con.fetchval(
                    "SELECT referrals FROM users WHERE user_id=$1", ref
                )
                if cnt >= PREMIUM_REFERRALS:
                    await con.execute(
                        "UPDATE users SET premium=TRUE, badge_type='invite' WHERE user_id=$1", ref
                    )

    user = await get_user(uid)
    if user["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back!", reply_markup=main_keyboard())

# ---------------- PROFILE FLOW (SAFE) ----------------
@dp.message(
    lambda m:
        m.text and
        m.from_user.id not in active_chats and
        m.text not in [
            "🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys",
            "📢 Invite & Earn Premium", "💎 VIP Status",
            "⏭ Next", "❌ Stop", "🚫 Block & Report", "✅ Unblock"
        ]
)

async def profile_flow(message: types.Message):
    if await check_banned(message):
        return

    uid = message.from_user.id
    user = await get_user(uid)
    text = message.text.strip()


    # 1️⃣ NAME
    if user["name"] is None:
        await update_user(uid, "name", text)
        await message.answer("🎂 Your age?")
        return

    # 2️⃣ AGE
    if user["age"] is None:
        if not text.isdigit() or not (10 <= int(text) <= 80):
            await message.answer("❌ Enter valid age (10–80)")
            return
        await update_user(uid, "age", int(text))
        await message.answer("📍 Your place?")
        return

    # 3️⃣ PLACE
    if user["place"] is None:
        await update_user(uid, "place", text)
        await message.answer("Select gender:", reply_markup=gender_keyboard())
        return

    # 4️⃣ GENDER
    if user["gender"] is None:
        if text not in ["👦 Boy", "👧 Girl"]:
            return  # invalid input, ignore

        await update_user(uid, "gender", text)
        waiting_random.add(uid)

        if text == "👧 Girl":
            waiting_find_girls.add(uid)
        else:
            waiting_find_boys.add(uid)

        await message.answer(
            "✅ Profile completed!\n💡 You are now discoverable",
            reply_markup=main_keyboard()
        )
        return




# ---------------- MATCH ----------------
async def try_match(uid, queue, want_gender, message):
    remove_from_all_queues(uid)
    me = await get_user(uid)
    msg = await message.answer("🔎 Searching...")

    for other_id in list(queue):
        if other_id == uid:
            continue
        if other_id in active_chats:
            queue.discard(other_id)
            continue

        other = await get_user(other_id)
        if not other:
            queue.discard(other_id)
            continue

        # block check
        if uid in blocked.get(other_id, []) or other_id in blocked.get(uid, []):
            continue

        # gender check
        if want_gender and other["gender"] != want_gender:
            continue

        # ✅ MATCH FOUND
        queue.discard(other_id)
        active_chats[uid] = other_id
        active_chats[other_id] = uid

        try: await msg.delete()
        except: pass

        # notify both
        try:
            await bot.send_message(uid, f"🎉 Match Found\n👤 {mask(other['name'])}", reply_markup=main_keyboard())
            await bot.send_message(other_id, f"🎉 Match Found\n👤 {mask(me['name'])}", reply_markup=main_keyboard())
        except Exception as e:
            print(f"MATCH NOTIFY ERROR: {e}")

        return

    queue.add(uid)
    try:
        await msg.edit_text("⏳ Waiting for partner...")
    except: pass

# ---------------- CHAT BUTTONS ----------------
@dp.message(lambda m: m.text in ["🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys"])
async def start_chat(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)

    if uid in active_chats:
        await message.answer("❌ Already in chat")
        return

    if message.text == "🔀 Random Chat (Free)":
        user_mode[uid] = "random"
        await try_match(uid, waiting_random, None, message)

    elif message.text == "👧 Find Girls":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        if user["gender"] != "👦 Boy":
            await message.answer("❌ Only boys can use Find Girls")
            return

        user_mode[uid] = "girls"
        await try_match(uid, waiting_find_girls, "👧 Girl", message)

    elif message.text == "👦 Find Boys":
        if not user["premium"]:
            await message.answer("💎 Premium required")
            return
        if user["gender"] != "👧 Girl":
            await message.answer("❌ Only girls can use Find Boys")
            return

        user_mode[uid] = "boys"
        await try_match(uid, waiting_find_boys, "👦 Boy", message)



# ---------------- NEXT / STOP ----------------
@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(message: types.Message):
    uid = message.from_user.id
    remove_from_all_queues(uid)

    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        remove_from_all_queues(pid)
        await bot.send_message(pid, "❌ Partner skipped")

    mode = user_mode.get(uid)

    if mode == "girls":
        await try_match(uid, waiting_find_girls, "👧 Girl", message)
    elif mode == "boys":
        await try_match(uid, waiting_find_boys, "👦 Boy", message)
    else:
        await try_match(uid, waiting_random, None, message)


@dp.message(lambda m: m.text == "❌ Stop")
async def stop_chat(message: types.Message):
    uid = message.from_user.id
    remove_from_all_queues(uid)

    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        remove_from_all_queues(pid)
        await bot.send_message(pid, "❌ Chat ended")

    await message.answer("✅ Chat stopped")

# ---------------- BLOCK / UNBLOCK ----------------
@dp.message(lambda m: m.text == "🚫 Block & Report")
async def block_user(message: types.Message):
    uid = message.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        blocked.setdefault(uid, []).append(pid)
        await bot.send_message(pid, "🚫 You were blocked")
    await message.answer("🚫 User blocked")

@dp.message(lambda m: m.text == "✅ Unblock")
async def unblock_user(message: types.Message):
    uid = message.from_user.id
    if uid not in blocked or not blocked[uid]:
        await message.answer("❌ No blocked users")
        return
    text = "Blocked users:\n" + "\n".join(map(str, blocked[uid]))
    await message.answer(text)

# ---------------- INVITE / VIP ----------------
@dp.message(lambda m: m.text == "📢 Invite & Earn Premium")
async def invite(message: types.Message):
    uid = message.from_user.id
    user = await get_user(uid)
    link = f"https://t.me/{(await bot.get_me()).username}?start={uid}"
    await message.answer(
        f"👥 Referrals: {user['referrals']}/{PREMIUM_REFERRALS}\n\n🔗 {link}"
    )

@dp.message(lambda m: m.text == "💎 VIP Status")
async def vip_status(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(
        f"💎 VIP: {'YES' if user['premium'] else 'NO'}\n"
        f"👥 Referrals: {user['referrals']}/{PREMIUM_REFERRALS}"
    )

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats)
async def relay_all(message: types.Message):
    uid = message.from_user.id
    pid = active_chats.get(uid)

    if not pid:
        return

    # block check
    if uid in blocked.get(pid, []) or pid in blocked.get(uid, []):
        return

    try:
        await message.copy_to(pid)
    except Exception as e:
        print("RELAY ERROR:", e)


# ---------------- ADMIN ----------------
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Not admin")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="bc")],
        [InlineKeyboardButton(text="🚫 Ban", callback_data="ban")],
        [InlineKeyboardButton(text="✅ Unban", callback_data="unban")],
        [InlineKeyboardButton(text="👑 Give VIP", callback_data="vip")]
    ])
    await message.answer("Admin Panel", reply_markup=kb)

@dp.callback_query(lambda c: c.data in ["bc","ban","unban","vip"])
async def admin_cb(c: CallbackQuery):
    admin_state[ADMIN_ID] = c.data
    await c.message.answer("Send user id / message")
    await c.answer()

@dp.message(lambda m: m.from_user.id == ADMIN_ID and ADMIN_ID in admin_state)
async def admin_action(message: types.Message):
    act = admin_state.pop(ADMIN_ID)
    if act == "ban":
        await ban_user(int(message.text))
        await message.answer("🚫 Banned")
    elif act == "unban":
        await unban_user(int(message.text))
        await message.answer("✅ Unbanned")
    elif act == "vip":
        await update_user(int(message.text), "premium", True)
        await update_user(int(message.text), "badge_type", "admin")
        await message.answer("👑 VIP granted")
    elif act == "bc":
        async with db_pool.acquire() as con:
            users = await con.fetch("SELECT user_id FROM users")
            for u in users:
                try:
                    await message.copy_to(u["user_id"])
                except: pass

# ---------------- RUN ----------------
async def main():
    await init_db()
    print("🚀 Minglo Bot Running (FULL)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())














