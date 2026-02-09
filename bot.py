import logging
import random
import asyncio
import os
import uuid
import threading
import time
from flask import Flask
from datetime import datetime
from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# --- FLASK SERVER FOR UPTIME ---
app = Flask('')

@app.route('/')
def home():
    return "Apex Cricket Bot is Online!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://username:password@cluster.mongodb.net/")
DB_NAME = "ApexCricketDB"
ADMIN_ID = 5298223577

# --- STYLING & TEXTS ---
DIVIDER_TOP = "◈───────────────────◈"
HEADER_TEXT = "         **APEX CRICKET WORLD**"
FOOTER_TEXT = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

RULES_SECTION = (
    "How to Play and Rules:\n"
    "1. Choose a number from 1 to 6.\n"
    "2. If your number matches the opponent, you are OUT.\n"
    "3. Score more than target to win the match.\n"
    "4. Max 2 wickets allowed per innings."
)

# Combined Start/Cricket Message - FIXED LINE 58 QUOTE
MAIN_MENU_TEXT = (
    f"{DIVIDER_TOP}\n{HEADER_TEXT}\n{DIVIDER_TOP}\n"
    "Welcome! Hand-Cricket on Telegram.\n"
    "Rules: 1 Over Match | 2 Wickets Max.\n\n"
    "• /cricket - Start New Match\n"
    "• /stats - Your Career Stats\n"
    "• /leaderboard - Global Rankings\n"
    "• /cancel Match ID - Stop Match\n"
    "🎯 Challenge Your Friend: Use /cricket @username or TG Numeric Id\n\n"
    f"{RULES_SECTION}\n\n"
    f"{FOOTER_TEXT}"
)

# --- DATABASE SETUP ---
client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
players_col = db["players"]
active_matches = {}

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- CLASSES ---

class Match:
    def __init__(self, chat_id, p1_id, p1_name, is_bot=False, target_user=None):
        self.match_id = str(uuid.uuid4())[:8]
        self.chat_id = chat_id
        self.p1 = {'id': p1_id, 'name': p1_name, 'runs': 0, 'wickets': 0, 'balls': 0}
        self.p2 = None
        self.is_bot = is_bot
        self.target_user = str(target_user) if target_user else None
        self.state = "WAITING"
        self.overs = 1
        self.wickets = 2
        self.batter = None
        self.bowler = None
        self.target_runs = None
        self.p1_move = None
        self.p2_move = None
        self.start_time = time.time()

        if is_bot:
            self.p2 = {'id': 0, 'name': 'Apex AI 🤖', 'runs': 0, 'wickets': 0, 'balls': 0}
            self.state = "TOSS_CALL"

# --- HELPERS ---

def get_mention(user_id, name):
    """Generates a deep link to user profile"""
    return f"[{name}](tg://user?id={user_id})"

def get_player(user_id, name="Player", username=None):
    p = players_col.find_one({"_id": user_id})
    if not p:
        p = {"_id": user_id, "name": name, "username": username, 
             "wins": 0, "losses": 0, "matches": 0, "total_runs": 0}
        players_col.insert_one(p)
    return p

async def auto_delete(chat_id, message_id, delay=20):
    """Deletes the result message after specified seconds"""
    await asyncio.sleep(delay)
    try:
        await Application.get_instance().bot.delete_message(chat_id, message_id)
    except:
        pass

# --- CORE COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_player(user.id, user.first_name, user.username)
    
    keyboard = [
        [InlineKeyboardButton("🏏 Play Cricket", callback_data="cb_cricket")],
        [InlineKeyboardButton("📊 My Stats", callback_data="cb_stats"), 
         InlineKeyboardButton("🏆 Leaderboard", callback_data="cb_lb")]
    ]
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def play_cricket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    args = context.args

    # Handle /cricket @user or Numeric ID
    if args and (args[0].startswith("@") or args[0].isdigit()):
        target = args[0]
        m = Match(chat.id, user.id, user.first_name, target_user=target)
        active_matches[m.match_id] = m
        text = (f"{DIVIDER_TOP}\n{HEADER_TEXT}\n{DIVIDER_TOP}\n"
                f"🎯 **Private Challenge!**\n👤 From: {get_mention(user.id, user.first_name)}\n"
                f"🎯 Target: {target}\n🆔 ID: `{m.match_id}`\n\nOnly target can join!\n\n{FOOTER_TEXT}")
        keyboard = [[InlineKeyboardButton("Join Challenge 🤝", callback_data=f"join_{m.match_id}")]]
        return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # Private Chat Logic (Only AI mode)
    if chat.type == "private":
        m = Match(chat.id, user.id, user.first_name, is_bot=True)
        active_matches[m.match_id] = m
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{m.match_id}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{m.match_id}_Tails")]]
        return await update.message.reply_text(f"{DIVIDER_TOP}\n🤖 **Apex AI Match**\n{DIVIDER_TOP}\nCall the toss:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # Group Logic (Show all modes)
    keyboard = [
        [InlineKeyboardButton("🤖 Play vs Apex AI", callback_data="mode_bot"),
         InlineKeyboardButton("👥 Friends Mode (Public)", callback_data="mode_public")]
    ]
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    p = get_player(user.id, user.first_name, user.username)
    
    wr = (p['wins']/p['matches']*100) if p['matches'] > 0 else 0
    msg = (f"{DIVIDER_TOP}\n📊 **STATS FOR {get_mention(user.id, user.first_name)}**\n{DIVIDER_TOP}\n"
           f"🏏 Matches: {p['matches']}\n🏆 Wins: {p['wins']}\n💀 Losses: {p['losses']}\n"
           f"📈 Runs: {p['total_runs']}\n🔥 Win Rate: {wr:.1f}%\n\n{FOOTER_TEXT}")
    
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    players = list(players_col.find({"matches": {"$gt": 0}}).sort("wins", -1).limit(10))
    
    text = f"{DIVIDER_TOP}\n🏆 **GLOBAL LEADERBOARD**\n{DIVIDER_TOP}\n"
    for i, p in enumerate(players, 1):
        mention = get_mention(p['_id'], p['name'])
        text += f"{i}. {mention} — {p['wins']} Wins\n"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text + f"\n{FOOTER_TEXT}", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text + f"\n{FOOTER_TEXT}", parse_mode=ParseMode.MARKDOWN)

async def cancel_match_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        return await update.message.reply_text("❌ Please provide Match ID.\nExample: `/cancel a1b2c3d4`", parse_mode=ParseMode.MARKDOWN)
    
    mid = context.args[0]
    if mid in active_matches:
        m = active_matches[mid]
        if user_id == m.p1['id'] or (m.p2 and user_id == m.p2['id']) or user_id == ADMIN_ID:
            del active_matches[mid]
            await update.message.reply_text(f"✅ Match `{mid}` has been cancelled successfully.")
        else:
            await update.message.reply_text("🚫 You are not a participant of this match!")
    else:
        await update.message.reply_text("❌ Invalid Match ID or match already finished.")

# --- CALLBACK HANDLER ---

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    # UI Buttons Logic
    if data == "cb_cricket": return await play_cricket_cmd(update, context)
    if data == "cb_stats": return await stats_cmd(update, context)
    if data == "cb_lb": return await leaderboard_cmd(update, context)

    # Mode Logic
    if data == "mode_bot":
        m = Match(query.message.chat_id, user.id, user.first_name, is_bot=True)
        active_matches[m.match_id] = m
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{m.match_id}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{m.match_id}_Tails")]]
        return await query.edit_message_text(f"{DIVIDER_TOP}\n🤖 **Apex AI Match**\nCall the toss:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    if data == "mode_public":
        m = Match(query.message.chat_id, user.id, user.first_name)
        active_matches[m.match_id] = m
        keyboard = [[InlineKeyboardButton("Join Match 🏏", callback_data=f"join_{m.match_id}")]]
        return await query.edit_message_text(f"{DIVIDER_TOP}\n🌍 **Public Match**\nHost: {user.first_name}\n🆔 ID: `{m.match_id}`\nWaiting...", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # Match Engine Interaction
    parts = data.split("_")
    if len(parts) < 2: return
    action, mid = parts[0], parts[1]
    
    if mid not in active_matches:
        return await query.answer("Match expired!", show_alert=True)
    m = active_matches[mid]

    if action == "join":
        if user.id == m.p1['id']: return await query.answer("You are the host!", show_alert=True)
        if m.target_user:
            target_clean = m.target_user.replace("@", "").lower()
            u_name = user.username.lower() if user.username else ""
            if m.target_user != str(user.id) and target_clean != u_name:
                return await query.answer("🔒 This is a private challenge!", show_alert=True)
        
        m.p2 = {'id': user.id, 'name': user.first_name, 'runs': 0, 'wickets': 0, 'balls': 0}
        m.state = "TOSS_CALL"
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{mid}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{mid}_Tails")]]
        await query.edit_message_text(f"{DIVIDER_TOP}\n🤝 {user.first_name} joined!\n{m.p1['name']}, call the toss:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    elif action == "toss":
        if user.id != m.p1['id']: return await query.answer("Only host calls!", show_alert=True)
        res = random.choice(["Heads", "Tails"])
        winner = m.p1 if parts[2] == res else m.p2
        if winner['id'] == 0: 
            await apply_choice(query, m, random.choice(["bat", "bowl"]), True)
        else:
            keyboard = [[InlineKeyboardButton("Bat", callback_data=f"choice_{mid}_bat"), InlineKeyboardButton("Bowl", callback_data=f"choice_{mid}_bowl")]]
            await query.edit_message_text(f"{DIVIDER_TOP}\n🪙 Result: {res}\n🎊 {winner['name']} won! Choice:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    elif action == "choice":
        await apply_choice(query, m, parts[2])

    elif action == "play":
        move = int(parts[2])
        if user.id == m.p1['id']: m.p1_move = move
        else: m.p2_move = move
        if m.is_bot: m.p2_move = random.randint(1, 6)
        if m.p1_move and m.p2_move: await engine(query, m)

# --- ENGINE LOGIC ---

async def apply_choice(query, m, choice, is_bot=False):
    p_who = m.p1 if (is_bot or query.from_user.id == m.p1['id']) else m.p2
    p_other = m.p2 if p_who == m.p1 else m.p1
    if choice == "bat": m.batter, m.bowler = p_who, p_other
    else: m.bowler, m.batter = p_who, p_other
    m.state = "INNINGS1"
    await render(query, m)

async def render(query, m, comm="Match Started!"):
    target = f"🎯 Target: {m.target_runs}" if m.target_runs else "First Innings"
    txt = (f"{DIVIDER_TOP}\n🏏 **{m.state}**\n{DIVIDER_TOP}\n{comm}\n\n"
           f"👤 Batter: {get_mention(m.batter['id'], m.batter['name'])}\n"
           f"👤 Bowler: {get_mention(m.bowler['id'], m.bowler['name'])}\n"
           f"📊 Score: {m.batter['runs']}/{m.batter['wickets']} ({m.batter['balls']//6}.{m.batter['balls']%6})\n"
           f"{target}\n\n{FOOTER_TEXT}")
    kb = [[InlineKeyboardButton(str(i), callback_data=f"play_{m.match_id}_{i}") for i in range(1, 4)],
          [InlineKeyboardButton(str(i), callback_data=f"play_{m.match_id}_{i}") for i in range(4, 7)]]
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def engine(query, m):
    bat_m = m.p1_move if m.batter['id'] == m.p1['id'] else m.p2_move
    bow_m = m.p1_move if m.bowler['id'] == m.p1['id'] else m.p2_move
    m.p1_move = m.p2_move = None
    m.batter['balls'] += 1
    
    if bat_m == bow_m:
        m.batter['wickets'] += 1
        comm = f"☝️ **OUT!** Both chose {bat_m}"
    else:
        m.batter['runs'] += bat_m
        comm = f"⚡ Score: {bat_m} runs"

    if m.batter['wickets'] >= 2 or m.batter['balls'] >= 6 or (m.target_runs and m.batter['runs'] >= m.target_runs):
        if m.state == "INNINGS1":
            m.target_runs = m.batter['runs'] + 1
            m.state = "INNINGS2"
            m.batter, m.bowler = m.bowler, m.batter
            await render(query, m, "🔄 Innings Over! Sides Swapped.")
        else:
            await finish_match(query, m)
    else:
        await render(query, m, comm)

async def finish_match(query, m):
    if m.p1['runs'] > m.p2['runs']: 
        win, res = m.p1, f"🏆 {m.p1['name']} Won!"
    elif m.p2['runs'] > m.p1['runs']: 
        win, res = m.p2, f"🏆 {m.p2['name']} Won!"
    else: 
        win, res = None, "🤝 Match Tied!"
    
    for p in [m.p1, m.p2]:
        if p['id'] != 0:
            players_col.update_one({"_id": p['id']}, {"$inc": {"matches": 1, "total_runs": p['runs']}})
    if win and win['id'] != 0: 
        players_col.update_one({"_id": win['id']}, {"$inc": {"wins": 1}})
    
    final_text = (f"{DIVIDER_TOP}\n{HEADER_TEXT}\n{DIVIDER_TOP}\nMatch Result:\n\n"
                  f"{m.p1['name']}: {m.p1['runs']} runs\n"
                  f"{m.p2['name']}: {m.p2['runs']} runs\n\n"
                  f"{res}\n\n{FOOTER_TEXT}")
    
    msg = await query.edit_message_text(final_text, parse_mode=ParseMode.MARKDOWN)
    if m.match_id in active_matches: del active_matches[m.match_id]
    
    asyncio.create_task(auto_delete(query.message.chat_id, msg.message_id, 20))

# --- ADMIN COMMANDS ---

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    total_users = players_col.count_documents({})
    live_matches = len(active_matches)
    await update.message.reply_text(f"🤖 **Bot Stats**\nTotal Users: {total_users}\nLive Matches: {live_matches}")

# --- MAIN ---

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cricket", play_cricket_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("cancel", cancel_match_cmd)) 
    app.add_handler(CommandHandler("botstats", bot_stats))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🚀 Apex Cricket World is Live!")
    app.run_polling()

if __name__ == "__main__":
    main()
