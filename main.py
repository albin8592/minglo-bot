import os 
import asyncio
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.storage.memory import MemoryStorage

# ✅ Bot token & admin ID (Set environment variables BOT_TOKEN and ADMIN_ID)
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))


# ---------------- INIT ----------------
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ---------------- DATA ----------------
waiting_random = set()
waiting_girls = set()
waiting_boys = set()
active_chats = {}
blocked = {}
skips = {}
user_data = {}
banned_users = set()
admin_state = {}
demo_reply_count = {}   # track messages per demo chat
FREE_SKIP_LIMIT = 5
PREMIUM_REFERRALS = 100
VIP_TAG = "👑 VIP User\n"
demo_active = set()
DEMO_USERS = {
    10001: {"name": "Anu", "age": 21, "place": "Kochi", "gender": "👧 Girl"},
    10002: {"name": "Meera", "age": 22, "place": "Calicut", "gender": "👧 Girl"},
    10003: {"name": "Aiswarya", "age": 23, "place": "Trissur", "gender": "👧 Girl"},
    10004: {"name": "Sneha", "age": 21, "place": "Alappuzha", "gender": "👧 Girl"},
    10005: {"name": "Neethu", "age": 22, "place": "Kollam", "gender": "👧 Girl"},
    10006: {"name": "Arya", "age": 23, "place": "Kannur", "gender": "👧 Girl"},
    10007: {"name": "Kavya", "age": 21, "place": "Kottayam", "gender": "👧 Girl"},
    10008: {"name": "Riya", "age": 22, "place": "Palakkad", "gender": "👧 Girl"},
    10009: {"name": "Divya", "age": 24, "place": "Malappuram", "gender": "👧 Girl"},
    10010: {"name": "Pooja", "age": 23, "place": "Idukki", "gender": "👧 Girl"},

    20001: {"name": "Rahul", "age": 23, "place": "Kochi", "gender": "👦 Boy"},
    20002: {"name": "Arjun", "age": 24, "place": "Calicut", "gender": "👦 Boy"},
    20003: {"name": "Akhil", "age": 22, "place": "Trivandrum", "gender": "👦 Boy"},
    20004: {"name": "Nithin", "age": 23, "place": "Thrissur", "gender": "👦 Boy"},
    20005: {"name": "Vishnu", "age": 24, "place": "Aluva", "gender": "👦 Boy"},
    20006: {"name": "Sreejith", "age": 25, "place": "Kollam", "gender": "👦 Boy"},
    20007: {"name": "Amal", "age": 22, "place": "Kannur", "gender": "👦 Boy"},
    20008: {"name": "Abhi", "age": 23, "place": "Palakkad", "gender": "👦 Boy"},
    20009: {"name": "Kiran", "age": 24, "place": "Kottayam", "gender": "👦 Boy"},
    20010: {"name": "Sachin", "age": 25, "place": "Malappuram", "gender": "👦 Boy"},
}
DEMO_REPLIES = [
    "Hi 🙂",
    "Hello!",
    "How are you?",
    "Nice to meet you",
    "Where are you from?",
    "What are you doing now?",
    "🙂",
    "That's nice",
    "Oh okay",
    "Haha 😄"
]

demo_reply_count = {}
def load_demo_users():
    for uid, d in DEMO_USERS.items():
        user_data[uid] = {
            "name": d["name"],
            "age": d["age"],
            "place": d["place"],
            "gender": d["gender"],
            "premium": True,
            "referrals": 0,
            "badge_type": None
        }
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

# ---------------- PREMIUM PROMO MESSAGE ----------------
async def send_premium_promo(uid):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Get Premium", callback_data="get_premium")],
        [InlineKeyboardButton(text="📢 Invite Friends", callback_data="invite_friends")]
    ])

    text = (
        "👋 Hello!\n\n"
        "💜 Welcome to Minglo Chat!\n\n"
        "🚀 Upgrade to Premium and enjoy:\n"
        "👧 Chat with Girls directly\n"
        "⚡ Unlimited skips\n"
        "👑 VIP badge\n"
        "🔥 Faster & better matches\n\n"
        f"🎁 Invite {PREMIUM_REFERRALS} friends and get Premium FREE!\n\n"
        "👇 Tap a button below to continue"
    )

    await bot.send_message(uid, text, reply_markup=keyboard)

# ---------------- BANNED CHECK ----------------
async def check_banned(message: types.Message):
    uid = message.from_user.id
    if uid in banned_users:
        await message.answer("🚫 You are banned from using this bot.\nPlease contact admin to request unban.")
        return True
    return False

# ---------------- START COMMAND ----------------
@dp.message(Command("start"))
async def start(message: types.Message):
    if await check_banned(message):
        return

    uid = message.from_user.id
    args = message.text.split()

    if uid not in user_data:
        user_data[uid] = {
            "name": None,
            "age": None,
            "place": None,
            "gender": None,
            "premium": False,
            "referrals": 0,
            "badge_type": None
        }
        skips[uid] = 0
        # Referral system
        if len(args) > 1 and args[1].isdigit():
            ref_id = int(args[1])
            if ref_id != uid and ref_id in user_data:
                user_data[ref_id]["referrals"] += 1
                if user_data[ref_id]["referrals"] >= PREMIUM_REFERRALS:
                    user_data[ref_id]["premium"] = True
                    user_data[ref_id]["badge_type"] = "invite"
                try:
                    await bot.send_message(
                        ref_id,
                        f"🎉 New referral joined!\n👥 {user_data[ref_id]['referrals']}/{PREMIUM_REFERRALS} referrals"
                        f"\nVIP Status: {'🎉 Invite VIP' if user_data[ref_id]['badge_type']=='invite' else ''}"
                    )
                except:
                    pass

    if user_data[uid]["name"] is None:
        await message.answer("👤 Your name?")
    else:
        await message.answer("💜 Welcome back to Minglo!", reply_markup=main_keyboard())

# ---------------- PREMIUM COMMAND ----------------
@dp.message(Command("premium"))
async def premium_command(message: types.Message):
    if await check_banned(message):
        return

    uid = message.from_user.id

    if uid not in user_data:
        await message.answer("❌ Please /start first")
        return

    if user_data[uid]["premium"]:
        await message.answer("👑 You already have Premium VIP!")
        return

    invite_link = f"https://t.me/{(await bot.get_me()).username}?start={uid}"

    await message.answer(
        "💎 Get Premium VIP\n\n"
        "✅ Chat with Girls\n"
        "✅ Unlimited Skips\n"
        "✅ VIP Badge\n\n"
        f"🎁 Invite {PREMIUM_REFERRALS} friends to get Premium FREE!\n\n"
        "🔗 Your Invite Link:\n"
        f"{invite_link}"
    )

# ---------------- PROFILE SETUP ----------------
@dp.message(lambda m: m.from_user.id in user_data and user_data[m.from_user.id]["name"] is None)
async def set_name(message: types.Message):
    if await check_banned(message):
        return
    user_data[message.from_user.id]["name"] = message.text
    await message.answer("🎂 Age (18+)?")

@dp.message(lambda m: m.from_user.id in user_data and user_data[m.from_user.id]["age"] is None)
async def set_age(message: types.Message):
    if await check_banned(message):
        return
    if not message.text.isdigit() or int(message.text) < 18:
        await message.answer("❌ 18+ only")
        return
    user_data[message.from_user.id]["age"] = int(message.text)
    await message.answer("📍 Place?")

@dp.message(lambda m: m.from_user.id in user_data and user_data[m.from_user.id]["place"] is None)
async def set_place(message: types.Message):
    if await check_banned(message):
        return
    user_data[message.from_user.id]["place"] = message.text
    await message.answer("Select your gender:", reply_markup=gender_keyboard())

@dp.message(lambda m: m.from_user.id in user_data and user_data[m.from_user.id]["gender"] is None)
async def set_gender(message: types.Message):
    if await check_banned(message):
        return
    if message.text not in ["👦 Boy", "👧 Girl"]:
        return

    uid = message.from_user.id
    user_data[uid]["gender"] = message.text

    await message.answer(
        "✅ Profile completed successfully!",
        reply_markup=main_keyboard()
    )

    # Send Premium promo
    await send_premium_promo(uid)

# ---------------- VIP STATUS ----------------
@dp.message(lambda m: m.text == "💎 VIP Status")
async def vip_status(message: types.Message):
    if await check_banned(message):
        return
    uid = message.from_user.id
    if uid not in user_data:
        await message.answer("❌ Please /start first")
        return
    status = "🎉 Invite VIP" if user_data[uid]["badge_type"]=="invite" else ""
    status = "👑 Admin VIP" if user_data[uid]["badge_type"]=="admin" else status
    await message.answer(
        f"💎 VIP Status: {status or 'None'}\n"
        f"👥 Referrals: {user_data[uid]['referrals']}/{PREMIUM_REFERRALS}"
    )

# ---------------- SUPPORT COMMAND ----------------
@dp.message(Command("support"))
async def support_command(message: types.Message):
    if await check_banned(message):
        return
    await message.answer(
        "🆘 Support\n\n"
        "If you face any issues, please contact us:\n\n"
        "📩 Admin: @your_admin_username\n"
        "⏰ Response time: Within 24 hours"
    )

# ---------------- MATCH ENGINE ----------------
import random  # ensure already imported
def mask_name(name: str) -> str:
    if not name: return "User"
    name = name.strip()
    if len(name) <= 2: return name[0] + "*"
    return name[0] + "***" + name[-1]

async def demo_auto_reply(demo_id, user_id):
    await asyncio.sleep(random.randint(2, 5))

    reply = random.choice(DEMO_REPLIES)
    try:
        await bot.send_message(user_id, reply)
    except:
        return

    demo_reply_count[user_id] = demo_reply_count.get(user_id, 0) + 1

    if demo_reply_count[user_id] >= random.randint(3, 6):
        await asyncio.sleep(2)
        try:
            await bot.send_message(user_id, "❌ Partner left the chat")
        except:
            pass

        active_chats.pop(user_id, None)
        active_chats.pop(demo_id, None)
        demo_reply_count.pop(user_id, None)

async def match_user(uid, pool, target_gender, message, allow_demo=True):
    """
    uid: Current user ID
    pool: waiting set (waiting_random / waiting_girls / waiting_boys)
    target_gender: "👧 Girl" / "👦 Boy" / None
    allow_demo: True -> Random Chat, False -> Gender-specific premium
    """

    if uid in banned_users:
        return

    searching_msg = await message.answer("🔎 Searching...")

    # 1️⃣ REAL USERS FIRST
    real_candidates = [
        user for user in pool
        if user != uid
        and user not in banned_users
        and user in user_data
        and (target_gender is None or user_data[user]["gender"] == target_gender)
        and uid not in blocked.get(user, [])
        and user not in active_chats
    ]
    random.shuffle(real_candidates)  # Random order

    if real_candidates:
        partner = real_candidates[0]
        pool.discard(partner)
        active_chats[uid] = partner
        active_chats[partner] = uid
        await searching_msg.delete()

        # Notify partner
        await bot.send_message(
            partner,
            f"🎉 Match found!\n"
            f"Name: {mask_name(user_data[uid]['name'])}\n"
            f"Age: {user_data[uid]['age']}\n"
            f"Place: {user_data[uid]['place']}",
            reply_markup=main_keyboard()
        )

        # Notify user
        await message.answer(
            f"🎉 Match found!\n"
            f"Name: {mask_name(user_data[partner]['name'])}\n"
            f"Age: {user_data[partner]['age']}\n"
            f"Place: {user_data[partner]['place']}",
            reply_markup=main_keyboard()
        )

        # Play tune
        try:
            await bot.send_audio(uid, open("match_tune.mp3", "rb"))
            await bot.send_audio(partner, open("match_tune.mp3", "rb"))
        except:
            pass

        return  # ✅ Real match done

    # 2️⃣ DEMO USERS ONLY FOR RANDOM CHAT
    if allow_demo:
        demo_list = list(demo_active)
        random.shuffle(demo_list)
        for demo in demo_list:
            if demo not in active_chats and (target_gender is None or user_data[demo]["gender"] == target_gender):
                active_chats[uid] = demo
                active_chats[demo] = uid
                await searching_msg.delete()

                await message.answer(
                    f"🎉 Match found!\n"
                    f"Name: {mask_name(user_data[demo]['name'])}\n"
                    f"Age: {user_data[demo]['age']}\n"
                    f"Place: {user_data[demo]['place']}",
                    reply_markup=main_keyboard()
                )

                # Start demo auto reply
                asyncio.create_task(demo_auto_reply(demo, uid))
                return

    # 3️⃣ NO MATCH → wait in pool
    pool.add(uid)
    await searching_msg.edit_text("⏳ Waiting for a partner...")




# ---------------- CHAT COMMANDS ----------------
@dp.message(lambda m: m.text in ["🔀 Random Chat (Free)", "👧 Find Girls", "👦 Find Boys"])
async def start_chat(message: types.Message):
    if await check_banned(message):
        return

    uid = message.from_user.id

    # ✅ Check if user is already in a chat
    if uid in active_chats:
        await message.answer("❌ You are already in a chat! Use ❌ Stop to end current chat first.")
        return

    # 🔀 Random Chat
    if message.text == "🔀 Random Chat (Free)":
        await match_user(uid, waiting_random, None, message)

    # 👧 Find Girls
    elif message.text == "👧 Find Girls":
        if not user_data[uid]["premium"]:
            await message.answer(f"💎 Premium Required\nInvite {PREMIUM_REFERRALS} friends to unlock.")
            return
        await match_user(uid, waiting_girls, "👧 Girl", message)

    # 👦 Find Boys
    elif message.text == "👦 Find Boys":
        if not user_data[uid]["premium"]:
            await message.answer(f"💎 Premium Required\nInvite {PREMIUM_REFERRALS} friends to unlock.")
            return
        await match_user(uid, waiting_boys, "👦 Boy", message)


@dp.message(lambda m: m.text == "⏭ Next")
async def next_chat(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    if not user_data[uid]["premium"] and skips[uid] >= FREE_SKIP_LIMIT:
        await message.answer("💎 Skip limit reached\nInvite friends to unlock Premium")
        return
    skips[uid] += 1
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Partner skipped")
    await random_chat(message)

@dp.message(lambda m: m.text == "❌ Stop")
async def stop(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        await bot.send_message(pid, "❌ Chat ended")
    await message.answer("✅ Chat stopped")

# ---------------- BLOCK & UNBLOCK ----------------
@dp.message(lambda m: m.text == "🚫 Block & Report")
async def block_user(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    if uid in active_chats:
        pid = active_chats.pop(uid)
        active_chats.pop(pid, None)
        blocked.setdefault(uid, []).append(pid)
        await bot.send_message(pid, "🚫 You were blocked by your partner")
    await message.answer("🚫 User blocked")

@dp.message(lambda m: m.text == "✅ Unblock")
async def unblock_user(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    blocked_list = blocked.get(uid, [])
    if not blocked_list:
        await message.answer("❌ No users to unblock")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=str(user), callback_data=f"unblock_{user}")] for user in blocked_list]
    )
    await message.answer("Select user to unblock:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data and c.data.startswith("unblock_"))
async def handle_unblock(callback: CallbackQuery):
    uid = callback.from_user.id
    target_id = int(callback.data.split("_")[1])
    if target_id in blocked.get(uid, []):
        blocked[uid].remove(target_id)
        await callback.message.edit_text(f"✅ User {target_id} unblocked")
    else:
        await callback.message.edit_text("❌ User not found in block list")

# ---------------- ADMIN PANEL ----------------
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ You are not admin")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🚫 Ban User", callback_data="admin_ban")],
        [InlineKeyboardButton(text="✅ Unban User", callback_data="admin_unban")],
        [InlineKeyboardButton(text="👑 Give VIP", callback_data="admin_premium")],
        [InlineKeyboardButton(text="👥 View Users", callback_data="admin_users")],
        [InlineKeyboardButton(text="❌ Close Panel", callback_data="admin_close")]
    ])
    await message.answer("✅ Admin Panel", reply_markup=keyboard)

# ---------------- ADMIN CALLBACKS ----------------
@dp.callback_query(lambda c: c.data and c.data.startswith("admin_"))
async def admin_callbacks(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Not allowed", show_alert=True)
        return
    data = callback.data
    await callback.answer()
    if data == "admin_users":
        if not user_data:
            await callback.message.edit_text("❌ No users yet")
            return
        text = "👥 Users List:\n\n"
        for uid, info in user_data.items():
            text += (
                f"ID: {uid}\nName: {info['name']}\nAge: {info['age']}\nPlace: {info['place']}\n"
                f"Gender: {info['gender']}\nPremium: {info['premium']}\nReferrals: {info['referrals']}\n"
                "-----------------\n"
            )
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

# ---------------- ADMIN ACTIONS ----------------
@dp.message(lambda m: m.from_user.id == ADMIN_ID and ADMIN_ID in admin_state)
async def admin_actions(message: types.Message):
    action = admin_state[ADMIN_ID]
    if action == "broadcast":
        sent = failed = 0
        for uid in user_data:
            if uid in banned_users: continue
            try:
                await message.copy_to(uid)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        await message.answer(f"✅ Broadcast completed\n📤 Sent: {sent}\n❌ Failed: {failed}")
    elif action == "ban":
        try:
            uid = int(message.text)
            banned_users.add(uid)
            if uid in active_chats:
                pid = active_chats.pop(uid)
                active_chats.pop(pid, None)
                await bot.send_message(pid, "❌ Your partner was banned. Chat ended.")
            waiting_random.discard(uid)
            waiting_girls.discard(uid)
            waiting_boys.discard(uid)
            await message.answer(f"🚫 User {uid} banned")
        except:
            await message.answer("❌ Invalid user ID")
    elif action == "unban":
        try:
            uid = int(message.text)
            banned_users.discard(uid)
            await message.answer(f"✅ User {uid} unbanned")
        except:
            await message.answer("❌ Invalid user ID")
    elif action == "premium":
        try:
            uid = int(message.text)
            if uid in user_data:
                user_data[uid]["premium"] = True
                user_data[uid]["badge_type"] = "admin"
                await message.answer(f"👑 User {uid} granted VIP")
            else:
                await message.answer("❌ User not found")
        except:
            await message.answer("❌ Invalid user ID")
    admin_state.pop(ADMIN_ID, None)

# ---------------- RELAY ----------------
@dp.message(lambda m: m.from_user.id in active_chats and not m.text.startswith("/"))
async def relay(message: types.Message):
    uid = message.from_user.id
    if uid in banned_users:
        return

    pid = active_chats.get(uid)
    if not pid:
        return

    # ---------- DEMO USER LOGIC ----------
    if pid in demo_active:
        # user message ignore cheyyam / optional
        asyncio.create_task(demo_auto_reply(pid, uid))
        return
    # ------------------------------------

    badge_type = user_data.get(uid, {}).get("badge_type")
    badge_text = ""
    if badge_type == "invite":
        badge_text = "🎉 Invite VIP\n"
    elif badge_type == "admin":
        badge_text = "👑 Premium VIP\n"

    if message.text:
        await bot.send_message(pid, badge_text + message.text)
    elif message.photo:
        caption = message.caption or ""
        await bot.send_photo(pid, message.photo[-1].file_id, caption=badge_text + caption)


# ---------------- INVITE BUTTON ----------------
@dp.message(lambda m: m.text == "📢 Invite & Earn Premium")
async def invite_button_handler(message: types.Message):
    if await check_banned(message): return
    uid = message.from_user.id
    invite_link = f"https://t.me/{(await bot.get_me()).username}?start={uid}"
    count = user_data[uid]["referrals"]
    await message.answer(
        "📢 Invite Friends & Earn Premium\n\n"
        f"👥 Your Referrals: {count}/{PREMIUM_REFERRALS}\n\n"
        "💎 Premium Benefits:\n"
        "👧 Chat with Girls\n⚡ Unlimited Skips\n👑 VIP Badge\n\n"
        f"🔗 Your Invite Link:\n{invite_link}"
    )

# ---------------- CALLBACKS FOR PREMIUM ----------------
@dp.callback_query(lambda c: c.data == "get_premium")
async def get_premium_callback(callback: CallbackQuery):
    uid = callback.from_user.id
    if user_data.get(uid, {}).get("premium"):
        await callback.answer("✅ You already have Premium!", show_alert=True)
        return
    await callback.message.answer(
        "💎 Premium Required\n👧 Chat with Girls\n⚡ Unlimited Skips\n👑 VIP Badge\n"
        f"🎁 Invite {PREMIUM_REFERRALS} friends to get Premium FREE!"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "invite_friends")
async def invite_friends_callback(callback: CallbackQuery):
    uid = callback.from_user.id
    invite_link = f"https://t.me/{(await bot.get_me()).username}?start={uid}"
    await callback.message.answer(
        "📢 Invite Friends & Earn Premium!\n\n"
        f"🔗 Your Invite Link:\n{invite_link}\n"
        f"👥 Invite {PREMIUM_REFERRALS} friends to unlock Premium!"
    )
    await callback.answer()

# ---------------- ABOUT ----------------
@dp.message(Command("about"))
async def about_command(message: types.Message):
    await message.answer(
        "💜 About Minglo Chat Bot\n\n"
        "Minglo is a random chat & matchmaking bot where you can:\n\n"
        "🔀 Chat with random people\n"
        "👧 Chat with girls (Premium)\n"
        "⚡ Skip chats instantly\n"
        "👑 Earn VIP via referrals\n\n"
        "🚀 Built for fun, privacy & fast matching.\n"
        "🛠 Developed by Minglo Team"
    )

# ---------------- RUN BOT ----------------
async def main():
    load_demo_users()
    print("💜 Minglo Bot Running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
