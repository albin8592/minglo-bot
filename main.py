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
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS banned_users(
            user_id BIGINT PRIMARY KEY
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            id SERIAL PRIMARY KEY,
            sender BIGINT,
            receiver BIGINT,
            text TEXT,
            time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS stars_log(
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            stars INT,
            time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

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
        [KeyboardButton(text="🎁 Send Stars as Gift")],
        [KeyboardButton(text="💎 VIP Status")],
        [KeyboardButton(text="⏭ Next"), KeyboardButton(text="❌ Stop")],
        [KeyboardButton(text="🚫 Block & Report"), KeyboardButton(text="✅ Unblock")],
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

def mask(user):
    """
    user: dict from DB containing 'name' and 'premium'
    Returns masked name with VIP badge if premium
    """
    name = user.get("name") or "User"
    masked = name[0] + "***"
    if user.get("premium"):
        masked += " 👑"
    return masked


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

 # ---------------- STARS GIFT MENU ----------------
@dp.message(lambda m: m.text == "🎁 Send Stars as Gift")
async def stars_menu(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 10 Stars", callback_data="stars_10")],
        [InlineKeyboardButton(text="⭐ 50 Stars", callback_data="stars_50")],
        [InlineKeyboardButton(text="⭐ 100 Stars", callback_data="stars_100")],
    ])

    await message.answer(
        "🎁 Support Minglo Bot\n\n"
        "Stars help us run & improve the bot 💜",
        reply_markup=kb
    
# ---------------- PROFILE FLOW (SAFE) ----------------
@dp.message(
    lambda m:
        m.text and
        not m.text.startswith("/") and
        m.from_user.id not in active_chats and
        m.from_user.id not in admin_state and   # 🔥 VERY IMPORTANT
       m.text not in [
    "🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys",
    "📢 Invite & Earn Premium", "🎁 Send Stars as Gift",
    "💎 VIP Status",
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

        try:
            await msg.delete()
        except Exception:
            pass

        # notify both
        try:
            await bot.send_message(uid, f"🎉 Match Found\n👤 {mask(other)}", reply_markup=main_keyboard())
            await bot.send_message(other_id, f"🎉 Match Found\n👤 {mask(me)}", reply_markup=main_keyboard())
        except Exception as e:
            print(f"MATCH NOTIFY ERROR: {e}")

        return

    queue.add(uid)
    try:
        await msg.edit_text("⏳ Waiting for partner...")
    except Exception:
        pass

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

# ---------------- UNBLOCK ----------------
@dp.message(lambda m: m.text == "✅ Unblock")
async def unblock_user(message: types.Message):
    uid = message.from_user.id
    if uid not in blocked or not blocked[uid]:
        await message.answer("❌ You have no blocked users")
        return

    # Show blocked users with numbers
    text = "Blocked users:\n" + "\n".join(
        [f"{i+1}. {user_id}" for i, user_id in enumerate(blocked[uid])]
    )
    text += "\n\nSend the number of the user you want to unblock."
    await message.answer(text)

    # Set state for unblocking
    admin_state[uid] = {"action": "unblock_user_step1"}

# Handle the number input to actually unblock
@dp.message(lambda m: m.from_user.id in admin_state and admin_state[m.from_user.id]["action"] == "unblock_user_step1")
async def unblock_user_step2(message: types.Message):
    uid = message.from_user.id
    if not message.text.isdigit():
        await message.answer("❌ Send a valid number.")
        return

    index = int(message.text) - 1
    if index < 0 or index >= len(blocked.get(uid, [])):
        await message.answer("❌ Invalid number.")
        return

    unblocked_id = blocked[uid].pop(index)
    await message.answer(f"✅ Unblocked user: {unblocked_id}")

    # Clear state
    admin_state.pop(uid, None)


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
    
    if not user['premium']:
        await message.answer(
            f"💎 VIP: NO\n"
            f"👥 Referrals: {user['referrals']}/{PREMIUM_REFERRALS}\n\n"
            "Upgrade to VIP to unlock special features!"
        )
        return

    # VIP features list
    vip_features = [
        "🔹 Access to 'Find Girls' / 'Find Boys' chats",
        "🔹 Unlimited ⏭ Next skips",
        "🔹 VIP badge 👑 next to your name",
        "🔹 Discoverable in premium-only searches",
        "🔹 Any future VIP perks"
    ]

    features_text = "\n".join(vip_features)

    await message.answer(
        f"💎 VIP: YES\n"
        f"👥 Referrals: {user['referrals']}/{PREMIUM_REFERRALS}\n\n"
        f"🎁 VIP Features:\n{features_text}"
    )
)

# ---------------- SUPPORT ----------------
@dp.message(Command("support"))
async def support(message: types.Message):
    if await check_banned(message): return
    await message.answer(
        "📞 Need help? Contact our support:\n"
        "Telegram: @YourSupportUsername\n"
        "Or reply here with your query."
    )


# ---------------- PREMIUM INFO ----------------
@dp.message(Command("premium"))
async def premium_info(message: types.Message):
    if await check_banned(message): return
    user = await get_user(message.from_user.id)
    
    if user["premium"]:
        await message.answer("💎 You are already a VIP!\nEnjoy premium features 🎉")
    else:
        link = f"https://t.me/{(await bot.get_me()).username}?start={message.from_user.id}"
        await message.answer(
            f"💎 Become VIP by inviting friends!\n"
            f"Referrals: {user['referrals']}/{PREMIUM_REFERRALS}\n"
            f"Invite link: {link}"
        )


# ---------------- ABOUT ----------------
@dp.message(Command("about"))
async def about(message: types.Message):
    if await check_banned(message): return
    await message.answer(
        "💜 Minglo Chat Bot\n\n"
        "🔹 Random Chat with strangers\n"
        "🔹 VIP features for premium users\n"
        "🔹 Block & Report functionality\n"
        "🔹 Find Boys / Girls for VIP users\n\n"
        "Enjoy safe & fun chatting! 💬"
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


# ---------------- ADMIN PANEL ----------------
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# memory dict for admin state
admin_state = {}  # user_id -> {"action": str}

# /admin command
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Not admin")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🚫 Ban User", callback_data="admin_ban")],
        [InlineKeyboardButton(text="✅ Unban User", callback_data="admin_unban")],
        [InlineKeyboardButton(text="👑 Give VIP", callback_data="admin_vip")],
        [InlineKeyboardButton(text="👥 View Users", callback_data="admin_view_users")]
    ])
    await message.answer("Admin Panel", reply_markup=kb)


# callback for buttons
@dp.callback_query(lambda c: c.from_user.id == ADMIN_ID and c.data.startswith("admin_"))
async def admin_cb(c: CallbackQuery):
    action = c.data
    admin_state[c.from_user.id] = {"action": action}

    if action == "admin_broadcast":
        await c.message.answer("📢 Send broadcast message:")
    elif action in ["admin_ban", "admin_unban", "admin_vip"]:
        await c.message.answer("Send the **user ID** (digits only):")
    elif action == "admin_view_users":
        async with db_pool.acquire() as con:
            users = await con.fetch("SELECT user_id, name, premium, referrals FROM users")
        if not users:
            await c.message.answer("No users found")
            return
        text = "👥 Users:\n\n" + "\n".join(
            f"ID: {u['user_id']}, Name: {u['name'] or 'N/A'}, VIP: {'YES' if u['premium'] else 'NO'}, Referrals: {u['referrals']}"
            for u in users
        )
        await c.message.answer(text)

    await c.answer()
# ---------------- STARS CALLBACK → INVOICE ----------------
@dp.callback_query(lambda c: c.data.startswith("stars_"))
async def send_stars_invoice(callback: CallbackQuery, bot: Bot):
    stars = int(callback.data.split("_")[1])

    prices = [types.LabeledPrice(
        label=f"{stars} Telegram Stars",
        amount=stars   # ⭐ Stars = amount
    )]

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="🎁 Support Minglo Bot",
        description=f"Gift {stars} Stars to support the bot 💜",
        payload=f"stars_{stars}",
        currency="XTR",  # Telegram Stars currency
        prices=prices
    )

    await callback.answer()
# ---------------- PRE-CHECKOUT (STARS) ----------------
@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: types.PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


# admin action handler
@dp.message(lambda m: m.from_user.id in admin_state)
async def admin_action(message: types.Message):
    state = admin_state[message.from_user.id]
    action = state["action"]

    try:
        # ---------------- BAN / UNBAN / VIP ----------------
        if action in ["admin_ban", "admin_unban", "admin_vip"]:
            text = message.text.strip()
            if not text.isdigit():
                await message.answer("❌ Invalid user ID")
                return
            uid = int(text)

            if action == "admin_ban":
                await ban_user(uid)
                await message.answer(f"🚫 User {uid} banned successfully.")

            elif action == "admin_unban":
                await unban_user(uid)
                await message.answer(f"✅ User {uid} unbanned successfully.")

            elif action == "admin_vip":
                await update_user(uid, "premium", True)
                await update_user(uid, "badge_type", "admin")
                await message.answer(f"👑 VIP granted to user {uid}.")

        # ---------------- BROADCAST ----------------
        elif action == "admin_broadcast":
            async with db_pool.acquire() as con:
                users = await con.fetch("SELECT user_id FROM users")

            success = 0
            failed = 0

            for u in users:
                try:
                    # TEXT
                    if message.text:
                        await bot.send_message(u["user_id"], message.text)
                    # PHOTO
                    elif message.photo:
                        await bot.send_photo(u["user_id"], message.photo[-1].file_id, caption=message.caption or "")
                    # VIDEO
                    elif message.video:
                        await bot.send_video(u["user_id"], message.video.file_id, caption=message.caption or "")
                    success += 1
                    await asyncio.sleep(0.05)
                except Exception as e:
                    failed += 1
                    print("Broadcast failed:", e)

            await message.answer(f"📢 Broadcast completed\n✅ Sent: {success}\n❌ Failed: {failed}")

    except Exception as e:
        await message.answer(f"❌ Error: {e}")

    # clear admin state
    admin_state.pop(message.from_user.id, None)

# ---------------- STARS PAYMENT SUCCESS ----------------
@dp.message(lambda m: m.successful_payment is not None)
async def stars_payment_success(message: types.Message):
    stars = message.successful_payment.total_amount  # ⭐ Stars count
    user_id = message.from_user.id

    # OPTIONAL: Save to DB
    try:
        async with db_pool.acquire() as con:
            await con.execute(
                "INSERT INTO stars_log (user_id, stars) VALUES ($1, $2)",
                user_id, stars
            )
    except:
        pass

    await message.answer(
        f"💜 Thank you!\n\n"
        f"🎁 You gifted *{stars} Stars*\n"
        f"Your support keeps the bot alive 🚀",
        parse_mode="Markdown"
    )

# ---------------- RUN ----------------
async def main():
    await init_db()
    print("🚀 Minglo Bot Running (FULL)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())










































