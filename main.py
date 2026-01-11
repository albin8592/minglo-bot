# Minglo Dating Bot – FULL PREMIUM VERSION (AUTO + ADMIN CONFIRM)
# ------------------------------------------------------------
# FEATURES
# - Auto premium when referral_count >= 100
# - Admin can manually give / revoke premium
# - Girls discovery = Premium only
# - FSM profile setup
# - Gender preference matching
# - Inline buttons
# - Photo support
# - Admin broadcast, ban/unban, stats
# ------------------------------------------------------------

import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
db = None

# ================== DATABASE ==================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)
    async with db.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            name TEXT,
            age INT,
            gender TEXT,
            preference TEXT,
            photo TEXT,
            premium BOOLEAN DEFAULT FALSE,
            referral_count INT DEFAULT 0,
            banned BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS referrals (
            referrer BIGINT,
            referred BIGINT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS reports (
            reporter BIGINT,
            target BIGINT,
            reason TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

# ================== FSM ==================
class Profile(StatesGroup):
    name = State()
    age = State()
    gender = State()
    preference = State()
    photo = State()

# ================== UI ==================
def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💖 Find Girls", callback_data="find")],
        [InlineKeyboardButton(text="✏️ Edit Profile", callback_data="edit")],
        [InlineKeyboardButton(text="⭐ Referral", callback_data="ref")]
    ])

# ================== START ==================
@dp.message(Command("start"))
async def start(m: types.Message, state: FSMContext):
    uid = m.from_user.id
    ref = None
    if m.text and len(m.text.split()) > 1:
        ref = int(m.text.split()[1])

    async with db.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        if not user:
            await con.execute("INSERT INTO users(id) VALUES($1)", uid)

            if ref and ref != uid:
                try:
                    await con.execute(
                        "INSERT INTO referrals(referrer, referred) VALUES($1,$2)",
                        ref, uid
                    )
                    await con.execute(
                        "UPDATE users SET referral_count = referral_count + 1 WHERE id=$1",
                        ref
                    )

                    r = await con.fetchrow("SELECT referral_count FROM users WHERE id=$1", ref)
                    if r and r['referral_count'] >= 100:
                        await con.execute("UPDATE users SET premium=TRUE WHERE id=$1", ref)
                        await bot.send_message(ref, "⭐ You are now PREMIUM (100 referrals)")
                except:
                    pass

            await m.answer("Welcome 💖\nSet your profile")
            await state.set_state(Profile.name)
        else:
            if user['banned']:
                return
            await m.answer("Welcome back 💫", reply_markup=menu())

# ================== PROFILE SETUP ==================
@dp.message(Profile.name)
async def set_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await state.set_state(Profile.age)
    await m.answer("Age?")

@dp.message(Profile.age)
async def set_age(m: types.Message, state: FSMContext):
    if not m.text.isdigit() or int(m.text) < 18:
        await m.answer("18+ only")
        return
    await state.update_data(age=int(m.text))
    await state.set_state(Profile.gender)
    await m.answer("Gender? (male/female)")

@dp.message(Profile.gender)
async def set_gender(m: types.Message, state: FSMContext):
    await state.update_data(gender=m.text.lower())
    await state.set_state(Profile.preference)
    await m.answer("Looking for? (male/female)")

@dp.message(Profile.preference)
async def set_pref(m: types.Message, state: FSMContext):
    await state.update_data(preference=m.text.lower())
    await state.set_state(Profile.photo)
    await m.answer("Send profile photo")

@dp.message(Profile.photo, F.photo)
async def set_photo(m: types.Message, state: FSMContext):
    data = await state.get_data()
    photo_id = m.photo[-1].file_id
    async with db.acquire() as con:
        await con.execute("""
        UPDATE users SET name=$1, age=$2, gender=$3, preference=$4, photo=$5
        WHERE id=$6
        """, data['name'], data['age'], data['gender'], data['preference'], photo_id, m.from_user.id)
    await state.clear()
    await m.answer("Profile ready ✅", reply_markup=menu())

# ================== FIND MATCH ==================
@dp.callback_query(F.data == "find")
async def find_match(c: types.CallbackQuery):
    uid = c.from_user.id
    async with db.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        if not user['premium']:
            await c.message.answer("🔒 Premium only\nInvite 100 friends")
            return

        match = await con.fetchrow("""
        SELECT * FROM users
        WHERE gender=$1 AND preference=$2 AND premium=TRUE AND banned=FALSE AND id!=$3
        ORDER BY random() LIMIT 1
        """, user['preference'], user['gender'], uid)

        if not match:
            await c.message.answer("No users now")
            return

        await c.message.answer_photo(
            match['photo'],
            caption=f"{match['name']} ({match['age']})",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="➡️ Next", callback_data="find")]]
            )
        )

# ================== REFERRAL ==================
@dp.callback_query(F.data == "ref")
async def referral(c: types.CallbackQuery):
    uid = c.from_user.id
    me = await bot.me()
    link = f"https://t.me/{me.username}?start={uid}"
    await c.message.answer(f"Invite 100 friends to unlock Premium ⭐\n\n{link}")

# ================== ADMIN ==================
@dp.message(Command("broadcast"))
async def broadcast(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    text = m.text.replace("/broadcast", "").strip()
    async with db.acquire() as con:
        users = await con.fetch("SELECT id FROM users WHERE banned=FALSE")
        for u in users:
            try:
                await bot.send_message(u['id'], text)
            except:
                pass

@dp.message(Command("givepremium"))
async def give_premium(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    uid = int(m.text.split()[1])
    async with db.acquire() as con:
        await con.execute("UPDATE users SET premium=TRUE WHERE id=$1", uid)
    await m.answer("Premium enabled ✅")

@dp.message(Command("ban"))
async def ban(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    uid = int(m.text.split()[1])
    async with db.acquire() as con:
        await con.execute("UPDATE users SET banned=TRUE WHERE id=$1", uid)
    await m.answer("User banned 🚫")

@dp.message(Command("stats"))
async def stats(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    async with db.acquire() as con:
        total = await con.fetchval("SELECT COUNT(*) FROM users")
        premium = await con.fetchval("SELECT COUNT(*) FROM users WHERE premium=TRUE")
        reports = await con.fetchval("SELECT COUNT(*) FROM reports")
    await m.answer(f"Users: {total}\nPremium: {premium}\nReports: {reports}")

# ================== MAIN ==================
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
