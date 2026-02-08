import logging
import random
import time
import asyncio
import os
import uuid
from datetime import datetime
from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    JobQueue
)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://username:password@cluster.mongodb.net/?retryWrites=true&w=majority")
DB_NAME = "HandCricketBot"
ADMIN_ID = 5298223577  # Replace with your Telegram User ID

# --- STYLING ---
DIVIDER = "◈───────────────────◈"
FOOTER = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

# --- DATABASE SETUP ---
client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
players_col = db["players"]
matches_history = db["match_logs"]

# --- GLOBAL SETTINGS ---
DEFAULT_OVERS = 2
DEFAULT_WICKETS = 2
active_matches = {}  # Store matches by unique match_id

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CLASSES ---

class Match:
    def __init__(self, chat_id, p1_id, p1_name, is_bot=False, target_user=None):
        self.match_id = str(uuid.uuid4())[:8]
        self.chat_id = chat_id
        self.p1 = {'id': p1_id, 'name': p1_name, 'runs': 0, 'wickets': 0, 'balls': 0}
        self.p2 = None
        self.is_bot = is_bot
        self.target_user = target_user  # For @challenges
        self.state = "WAITING" 
        self.overs = DEFAULT_OVERS
        self.wickets = DEFAULT_WICKETS
        self.batter = None
        self.bowler = None
        self.target = None
        self.p1_move = None
        self.p2_move = None
        self.start_time = time.time()

        if is_bot:
            self.p2 = {'id': 0, 'name': 'Apex Bot 🤖', 'runs': 0, 'wickets': 0, 'balls': 0}
            self.state = "TOSS_CALL"

# --- DB HELPERS ---

def get_player(user_id, username="Player"):
    player = players_col.find_one({"_id": user_id})
    if not player:
        player = {"_id": user_id, "username": username, "wins": 0, "losses": 0, "matches": 0}
        players_col.insert_one(player)
    return player

# --- COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_player(update.effective_user.id, update.effective_user.username)
    msg = (
        f"🏏 **Hand Cricket Pro**\n{DIVIDER}\n"
        "• `/cricket` - Start open match\n"
        "• `/cricket bot` - Play with Apex AI\n"
        "• `/challenge @user` - Specific challenge\n"
        "• `/cancel match_id` - Stop a match\n"
        "• `/stats` - Your profile\n"
        f"{FOOTER}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def play_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    args = context.args

    is_bot = len(args) > 0 and args[0].lower() == "bot"
    target_user = args[0] if (len(args) > 0 and args[0].startswith("@")) else None

    match = Match(chat_id, user.id, user.first_name, is_bot, target_user)
    active_matches[match.match_id] = match

    if is_bot:
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{match.match_id}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{match.match_id}_Tails")]]
        await update.message.reply_text(f"🤖 **Apex Mode Activated!**\nMatch ID: `{match.match_id}`\nCall the toss:", 
                                       reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        text = f"🏏 **New Match Hosted!**\n{DIVIDER}\n👤 Host: {user.first_name}\n🆔 ID: `{match.match_id}`"
        if target_user: text += f"\n🎯 Challenge for: {target_user}"
        else: text += f"\n🌍 Open for everyone!"
        
        keyboard = [[InlineKeyboardButton("Join Match 🏏", callback_data=f"join_{match.match_id}")]]
        await update.message.reply_text(text + FOOTER, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def cancel_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: `/cancel match_id`")
    
    mid = context.args[0]
    if mid in active_matches:
        m = active_matches[mid]
        if update.effective_user.id in [m.p1['id'], (m.p2['id'] if m.p2 else None)]:
            del active_matches[mid]
            await update.message.reply_text(f"✅ Match `{mid}` has been cancelled.")
        else:
            await update.message.reply_text("❌ Only participants can cancel this match.")
    else:
        await update.message.reply_text("❌ Invalid Match ID.")

# --- CALLBACK LOGIC ---

async def handle_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data.split("_")
    action = data[0]
    mid = data[1]

    if mid not in active_matches:
        return await query.answer("Match not found or expired.", show_alert=True)

    m = active_matches[mid]

    if action == "join":
        if user.id == m.p1['id']: return await query.answer("You are the host!", show_alert=True)
        if m.target_user and f"@{user.username}" != m.target_user:
            return await query.answer(f"This is a private challenge for {m.target_user}!", show_alert=True)
        
        m.p2 = {'id': user.id, 'name': user.first_name, 'runs': 0, 'wickets': 0, 'balls': 0}
        m.state = "TOSS_CALL"
        keyboard = [[InlineKeyboardButton("Heads", callback_data=f"toss_{mid}_Heads"),
                     InlineKeyboardButton("Tails", callback_data=f"toss_{mid}_Tails")]]
        await query.edit_message_text(f"🤝 **{user.first_name} Joined!**\n{m.p1['name']}, call the toss:", 
                                       reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif action == "toss":
        if user.id != m.p1['id']: return
        call = data[2]
        res = random.choice(["Heads", "Tails"])
        winner = m.p1 if call == res else m.p2
        m.state = "TOSS_CHOICE"
        
        if winner['id'] == 0: # Bot won toss
            choice = random.choice(["bat", "bowl"])
            await apply_choice(query, m, choice, is_bot_winner=True)
        else:
            keyboard = [[InlineKeyboardButton("Bat", callback_data=f"choice_{mid}_bat"),
                         InlineKeyboardButton("Bowl", callback_data=f"choice_{mid}_bowl")]]
            await query.edit_message_text(f"🪙 Result: {res}\n🎊 {winner['name']} won! Choose:", 
                                           reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "choice":
        choice = data[2]
        await apply_choice(query, m, choice)

    elif action == "play":
        move = int(data[2])
        if user.id == m.p1['id']: m.p1_move = move
        elif user.id == m.p2['id']: m.p2_move = move
        
        if m.is_bot: m.p2_move = random.randint(1, 6)
        
        if m.p1_move and m.p2_move:
            await process_engine(query, m)

async def apply_choice(query, m, choice, is_bot_winner=False):
    # Logic to set batter/bowler based on choice
    winner_id = 0 if is_bot_winner else query.from_user.id
    player_who_chose = m.p1 if winner_id == m.p1['id'] else m.p2
    other_player = m.p2 if winner_id == m.p1['id'] else m.p1

    if choice == "bat":
        m.batter, m.bowler = player_who_chose, other_player
    else:
        m.bowler, m.batter = player_who_chose, other_player
    
    m.state = "INNINGS1"
    await render_game(query, m)

async def render_game(query, m, commentary="Game Started!"):
    target_txt = f"🎯 Target: {m.target}" if m.target else "First Innings"
    text = (f"🏏 **{m.state}**\n{DIVIDER}\n{commentary}\n\n"
            f"👤 Batter: {m.batter['name']}\n"
            f"👤 Bowler: {m.bowler['name']}\n"
            f"📊 Score: {m.batter['runs']}/{m.batter['wickets']} ({m.batter['balls']//6}.{m.batter['balls']%6})\n"
            f"{target_txt}{FOOTER}")
    
    keyboard = [[InlineKeyboardButton(str(i), callback_data=f"play_{m.match_id}_{i}") for i in range(1, 4)],
                [InlineKeyboardButton(str(i), callback_data=f"play_{m.match_id}_{i}") for i in range(4, 7)]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def process_engine(query, m):
    bat_move = m.p1_move if m.batter['id'] == m.p1['id'] else m.p2_move
    bow_move = m.p1_move if m.bowler['id'] == m.p1['id'] else m.p2_move
    m.p1_move = m.p2_move = None
    m.batter['balls'] += 1
    
    if bat_move == bow_move:
        m.batter['wickets'] += 1
        comm = f"☝️ **OUT!** Both chose {bat_move}"
    else:
        m.batter['runs'] += bat_move
        comm = f"⚡ {m.batter['name']} hits a {bat_move}!"

    # Conditions for Innings End
    over_lim = m.overs * 6
    if m.batter['wickets'] >= m.wickets or m.batter['balls'] >= over_lim or (m.target and m.batter['runs'] >= m.target):
        if m.state == "INNINGS1":
            m.target = m.batter['runs'] + 1
            m.state = "INNINGS2"
            m.batter, m.bowler = m.bowler, m.batter
            await render_game(query, m, "🔄 Innings Swapped!")
        else:
            await finalize_match(query, m)
    else:
        await render_game(query, m, comm)

async def finalize_match(query, m):
    if m.p1['runs'] > m.p2['runs']: winner, loser = m.p1, m.p2
    elif m.p2['runs'] > m.p1['runs']: winner, loser = m.p2, m.p1
    else: winner = None

    res = f"🏆 **{winner['name']} wins!**" if winner else "🤝 **Match Tied!**"
    
    # Update DB for humans
    for p in [m.p1, m.p2]:
        if p['id'] != 0:
            players_col.update_one({"_id": p['id']}, {"$inc": {"matches": 1}})
    if winner and winner['id'] != 0:
        players_col.update_one({"_id": winner['id']}, {"$inc": {"wins": 1}})
    if winner and loser['id'] != 0:
        players_col.update_one({"_id": loser['id']}, {"$inc": {"losses": 1}})

    await query.edit_message_text(f"🏁 **Match Over**\n{DIVIDER}\n{m.p1['name']}: {m.p1['runs']}\n{m.p2['name']}: {m.p2['runs']}\n\n{res}{FOOTER}", parse_mode="Markdown")
    if m.match_id in active_matches: del active_matches[m.match_id]

# --- ADMIN FEATURES ---

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    msg = " ".join(context.args)
    users = players_col.find({}, {"_id": 1})
    count = 0
    for u in users:
        try:
            await context.bot.send_message(u['_id'], f"📢 **Announcement**\n{DIVIDER}\n{msg}{FOOTER}", parse_mode="Markdown")
            count += 1
        except: continue
    await update.message.reply_text(f"✅ Broadcast sent to {count} users.")

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    total_users = players_col.count_documents({})
    active = len(active_matches)
    await update.message.reply_text(f"🤖 **Bot Internal Stats**\n{DIVIDER}\n👥 Total Users: {total_users}\n🏏 Active Matches: {active}\n{FOOTER}", parse_mode="Markdown")

# --- BACKGROUND TASKS ---

async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    to_delete = []
    for mid, m in active_matches.items():
        if m.state == "WAITING" and (now - m.start_time) > 120:
            to_delete.append(mid)
    for mid in to_delete:
        try:
            await context.bot.send_message(active_matches[mid].chat_id, f"⏰ Match `{mid}` cancelled: No one joined within 2 minutes.")
            del active_matches[mid]
        except: pass

# --- MAIN ---

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cricket", play_cricket))
    app.add_handler(CommandHandler("cancel", cancel_match))
    app.add_handler(CommandHandler("stats", bot_stats)) # Reusing /stats for admin
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(handle_interaction))

    # Run cleanup every 60 seconds
    app.job_queue.run_repeating(cleanup_job, interval=60, first=10)

    print("🚀 Bot is live with Multi-Match & Apex AI support!")
    app.run_polling()

if __name__ == "__main__":
    main()
