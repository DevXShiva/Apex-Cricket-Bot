import logging
import random
import time
import asyncio
import os
import uuid
import threading
from flask import Flask
from datetime import datetime
from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://username:password@cluster.mongodb.net/?retryWrites=true&w=majority")
DB_NAME = "ApexCricketDB"
ADMIN_ID = 5298223577

# --- STYLING ---
HEADER = "◈───────────────────◈\n         **APEX CRICKET WORLD**\n◈───────────────────◈"
DIVIDER = "◈───────────────────◈"
FOOTER = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

# --- DATABASE ---
client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
players_col = db["players"]

# --- GLOBAL SETTINGS ---
active_matches = {}

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- LOGIC CLASSES ---

class Match:
    def __init__(self, chat_id, p1_id, p1_name, is_bot=False, target_user=None):
        self.match_id = str(uuid.uuid4())[:8]
        self.chat_id = chat_id
        self.p1 = {'id': p1_id, 'name': p1_name, 'runs': 0, 'wickets': 0, 'balls': 0}
        self.p2 = None
        self.is_bot = is_bot
        self.target_user = target_user 
        self.state = "WAITING"
        self.overs = 1
        self.wickets = 2
        self.batter = None
        self.bowler = None
        self.target = None
        self.p1_move = None
        self.p2_move = None
        self.start_time = time.time()

        if is_bot:
            self.p2 = {'id': 0, 'name': 'Apex AI 🤖', 'runs': 0, 'wickets': 0, 'balls': 0}
            self.state = "TOSS_CALL"

# --- HELPERS ---

def get_player(user_id, username="Player"):
    p = players_col.find_one({"_id": user_id})
    if not p:
        p = {"_id": user_id, "username": f"@{username}" if username else "User", 
             "wins": 0, "losses": 0, "matches": 0, "total_runs": 0}
        players_col.insert_one(p)
    return p

def get_leaderboard():
    players = list(players_col.find({"matches": {"$gt": 0}}))
    for p in players:
        p['wr'] = (p['wins'] / p['matches']) * 100
    sorted_p = sorted(players, key=lambda x: x['wr'], reverse=True)[:10]
    
    text = f"{HEADER}\n🏆 **GLOBAL LEADERBOARD**\n{DIVIDER}\n"
    for i, p in enumerate(sorted_p, 1):
        text += f"{i}. {p['username']} - {p['wr']:.1f}% WR ({p['wins']}W)\n"
    return text + FOOTER

# --- COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_player(user.id, user.username)
    
    msg = (
        f"{HEADER}\n"
        "Welcome! Hand-Cricket on Telegram.\nRules: 1 Over Match | 2 Wickets Max.\n"
        f"{DIVIDER}\n"
        "• `/cricket` - Start New Match\n"
        "• `/stats` - Your Career Stats\n"
        "• `/leaderboard` - Global Rankings\n"
        "• `/cancel ID` - Stop Match\n"
        f"{FOOTER}"
    )
    
    keyboard = [
        [InlineKeyboardButton("🏏 Play Cricket", callback_data="cmd_cricket")],
        [InlineKeyboardButton("📊 My Stats", callback_data="cmd_stats"), 
         InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd_lb")]
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def play_cricket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    # Handle direct challenges /cricket @user or ID
    if args and (args[0].startswith("@") or args[0].isdigit()):
        target = args[0]
        m = Match(update.effective_chat.id, user.id, user.first_name, target_user=target)
        active_matches[m.match_id] = m
        text = f"{HEADER}\n🎯 **Private Challenge!**\n{DIVIDER}\n👤 From: {user.first_name}\n🎯 Target: {target}\n🆔 ID: `{m.match_id}`\n\nOnly the challenged player can join!"
        keyboard = [[InlineKeyboardButton("Join Challenge 🤝", callback_data=f"join_{m.match_id}")]]
        return await update.message.reply_text(text + FOOTER, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Default Mode Selection
    text = (f"{HEADER}\n🏏 **Choose Match Mode**\n{DIVIDER}\n"
            "🤖 **Apex AI**: Fast 1v1 vs Bot\n"
            "👥 **Friends**: Public match in Group\n"
            "🎯 **Challenge**: Use `/cricket @user`")
    
    keyboard = [
        [InlineKeyboardButton("🤖 Play vs Apex AI", callback_data="mode_bot")],
        [InlineKeyboardButton("👥 Friends Mode (Public)", callback_data="mode_public")]
    ]
    await update.message.reply_text(text + FOOTER, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    if context.args:
        target = context.args[0]
        if target.isdigit():
            p = players_col.find_one({"_id": int(target)})
        else:
            p = players_col.find_one({"username": target if target.startswith("@") else f"@{target}"})
        if not p: return await update.message.reply_text("❌ User not found in database.")
    else:
        p = get_player(user_id, username)

    wr = (p['wins']/p['matches']*100) if p['matches'] > 0 else 0
    msg = (f"{HEADER}\n📊 **STATS FOR {p['username']}**\n{DIVIDER}\n"
           f"🏏 Matches: {p['matches']}\n🏆 Wins: {p['wins']}\n💀 Losses: {p['losses']}\n"
           f"🔥 Win Rate: {wr:.1f}%\n📈 Runs: {p['total_runs']}\n{FOOTER}")
    await update.message.reply_text(msg, parse_mode="Markdown")

# --- CALLBACK HANDLER ---

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    # UI Commands
    if data == "cmd_cricket": return await play_cricket_cmd(update, context)
    if data == "cmd_stats": return await stats_cmd(update, context)
    if data == "cmd_lb": return await query.edit_message_text(get_leaderboard(), parse_mode="Markdown", reply_markup=query.message.reply_markup)

    # Mode Selection
    if data == "mode_bot":
        m = Match(query.message.chat_id, user.id, user.first_name, is_bot=True)
        active_matches[m.match_id] = m
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{m.match_id}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{m.match_id}_Tails")]]
        return await query.edit_message_text(f"{HEADER}\n🤖 **Apex AI Match**\n{DIVIDER}\nCall the toss:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    if data == "mode_public":
        m = Match(query.message.chat_id, user.id, user.first_name)
        active_matches[m.match_id] = m
        keyboard = [[InlineKeyboardButton("Join Match 🏏", callback_data=f"join_{m.match_id}")]]
        return await query.edit_message_text(f"{HEADER}\n🌍 **Public Match Created**\n{DIVIDER}\nHost: {user.first_name}\n🆔 ID: `{m.match_id}`\nWaiting for anyone to join...", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    # Match Interactions
    parts = data.split("_")
    action = parts[0]
    if len(parts) < 2: return
    mid = parts[1]
    
    if mid not in active_matches:
        return await query.answer("Match expired! Start a new one.", show_alert=True)
    
    m = active_matches[mid]

    # Permission Guard
    if action in ["toss", "choice", "play"]:
        if user.id not in [m.p1['id'], (m.p2['id'] if m.p2 else None)]:
            return await query.answer("🚫 You are not a participant in this match!\nEnjoy the game as a spectator. 🍿", show_alert=True)

    if action == "join":
        if user.id == m.p1['id']: return await query.answer("You are the host!", show_alert=True)
        if m.target_user:
            is_match = False
            if m.target_user.isdigit() and int(m.target_user) == user.id: is_match = True
            elif m.target_user.lower() == f"@{user.username}".lower(): is_match = True
            if not is_match: return await query.answer(f"🔒 This challenge is only for {m.target_user}!", show_alert=True)
        
        m.p2 = {'id': user.id, 'name': user.first_name, 'runs': 0, 'wickets': 0, 'balls': 0}
        m.state = "TOSS_CALL"
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{mid}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{mid}_Tails")]]
        await query.edit_message_text(f"{HEADER}\n🤝 **Match Ready!**\n{DIVIDER}\n{m.p1['name']} vs {m.p2['name']}\n\n{m.p1['name']}, call the toss:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif action == "toss":
        if user.id != m.p1['id']: return
        res = random.choice(["Heads", "Tails"])
        winner = m.p1 if parts[2] == res else m.p2
        m.state = "TOSS_CHOICE"
        if winner['id'] == 0:
            choice = random.choice(["bat", "bowl"])
            await apply_choice(query, m, choice, True)
        else:
            keyboard = [[InlineKeyboardButton("Bat", callback_data=f"choice_{mid}_bat"), InlineKeyboardButton("Bowl", callback_data=f"choice_{mid}_bowl")]]
            await query.edit_message_text(f"{HEADER}\n🪙 Result: {res}\n🎊 {winner['name']} won! Choose side:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif action == "choice":
        await apply_choice(query, m, parts[2])

    elif action == "play":
        move = int(parts[2])
        if user.id == m.p1['id']: m.p1_move = move
        else: m.p2_move = move
        if m.is_bot: m.p2_move = random.randint(1, 6)
        if m.p1_move and m.p2_move: await engine(query, m)

async def apply_choice(query, m, choice, is_bot=False):
    p_who = m.p1 if (is_bot or query.from_user.id == m.p1['id']) else m.p2
    p_other = m.p2 if p_who == m.p1 else m.p1
    if choice == "bat": m.batter, m.bowler = p_who, p_other
    else: m.bowler, m.batter = p_who, p_other
    m.state = "INNINGS1"
    await render(query, m)

async def render(query, m, comm="Match Started!"):
    target = f"🎯 Target: {m.target}" if m.target else "First Innings"
    txt = (f"{HEADER}\n🏏 **{m.state}**\n{DIVIDER}\n{comm}\n\n"
           f"👤 Batter: {m.batter['name']}\n👤 Bowler: {m.bowler['name']}\n"
           f"📊 Score: {m.batter['runs']}/{m.batter['wickets']} ({m.batter['balls']//6}.{m.batter['balls']%6})\n"
           f"{target}{FOOTER}")
    kb = [[InlineKeyboardButton(str(i), callback_data=f"play_{m.match_id}_{i}") for i in range(1, 4)],
          [InlineKeyboardButton(str(i), callback_data=f"play_{m.match_id}_{i}") for i in range(4, 7)]]
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def engine(query, m):
    b_move = m.p1_move if m.batter['id'] == m.p1['id'] else m.p2_move
    bo_move = m.p1_move if m.bowler['id'] == m.p1['id'] else m.p2_move
    m.p1_move = m.p2_move = None
    m.batter['balls'] += 1
    if b_move == bo_move:
        m.batter['wickets'] += 1
        comm = f"☝️ **OUT!** Both chose {b_move}"
    else:
        m.batter['runs'] += b_move
        comm = f"⚡ {m.batter['name']} scores {b_move}!"

    if m.batter['wickets'] >= m.wickets or m.batter['balls'] >= 6 or (m.target and m.batter['runs'] >= m.target):
        if m.state == "INNINGS1":
            m.target = m.batter['runs'] + 1
            m.state = "INNINGS2"
            m.batter, m.bowler = m.bowler, m.batter
            await render(query, m, "🔄 Innings Over! Side Swapped.")
        else:
            await finish(query, m)
    else:
        await render(query, m, comm)

async def finish(query, m):
    if m.p1['runs'] > m.p2['runs']: win, lose = m.p1, m.p2
    elif m.p2['runs'] > m.p1['runs']: win, lose = m.p2, m.p1
    else: win = None
    
    if win:
        res = f"🏆 **{win['name']} Won!**"
        if win['id'] != 0: players_col.update_one({"_id": win['id']}, {"$inc": {"wins": 1, "matches": 1, "total_runs": win['runs']}})
        if lose['id'] != 0: players_col.update_one({"_id": lose['id']}, {"$inc": {"losses": 1, "matches": 1, "total_runs": lose['runs']}})
    else: res = "🤝 **Match Tied!**"

    await query.edit_message_text(f"{HEADER}\n🏁 **Match Result**\n{DIVIDER}\n"
                                   f"{m.p1['name']}: {m.p1['runs']} runs\n"
                                   f"{m.p2['name']}: {m.p2['runs']} runs\n\n"
                                   f"{res}{FOOTER}", parse_mode="Markdown")
    if m.match_id in active_matches: del active_matches[m.match_id]

# --- ADMIN ---

async def botstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    total = players_col.count_documents({})
    active = len(active_matches)
    await update.message.reply_text(f"{HEADER}\n🤖 **Bot Admin Stats**\n{DIVIDER}\nTotal Users: {total}\nLive Matches: {active}\n{FOOTER}", parse_mode="Markdown")

# --- MAIN ---

def main():
    # Start Flask in a separate thread
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cricket", play_cricket_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("leaderboard", lambda u, c: u.message.reply_text(get_leaderboard(), parse_mode="Markdown")))
    app.add_handler(CommandHandler("botstats", botstats))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🚀 Apex Cricket World is Live!")
    app.run_polling()

if __name__ == "__main__":
    main()
