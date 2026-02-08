import os
import random
import asyncio
import uuid
import time
import threading
import logging
from datetime import datetime
from collections import defaultdict
from typing import Optional, Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode, ChatType
from flask import Flask, jsonify

# ================= CONFIG =================
# Replace with your actual Token
BOT_TOKEN = os.getenv("BOT_TOKEN") 

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= FLASK KEEP-ALIVE =================
app = Flask(__name__)

@app.route('/')
def home(): return jsonify({"status": "online", "service": "APEX Cricket Bot"})

@app.route('/health')
def health(): return jsonify({"status": "healthy"})

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ================= IN-MEMORY STORAGE =================
active_matches = {}
user_matches = {}
group_matches = defaultdict(list)
private_invites = {}

MATCH_TIMEOUT = 300
MAX_MATCHES_PER_GROUP = 10

# ================= AI BOT SYSTEM =================
class AICricketBot:
    def __init__(self):
        self.name = "🤖 APEX AI"
        self.id = "ai_bot"

    def make_move(self):
        # Weighted random for realistic gameplay
        weights = [10, 20, 15, 20, 10, 25] 
        return random.choices([1, 2, 3, 4, 5, 6], weights=weights, k=1)[0]

# ================= MATCH MANAGEMENT =================
def create_match(chat_id, created_by, vs_ai=False, is_private=False, invited_user=None):
    if len(group_matches.get(str(chat_id), [])) >= MAX_MATCHES_PER_GROUP:
        return None

    match_id = str(uuid.uuid4())[:6].upper()

    match = {
        "match_id": match_id,
        "chat_id": str(chat_id),
        "created_by": str(created_by),
        "vs_ai": vs_ai,
        "ai_bot": AICricketBot() if vs_ai else None,
        "players": [],
        "state": "waiting",
        "score": 0,
        "wickets": 0,
        "overs": 0,
        "balls": 0,
        "target": None,
        "created_at": datetime.utcnow(),
        "last_activity": datetime.utcnow(),
        "toss_winner": None,
        "batting_first": None, 
        "inning": 1,
        "current_choices": {} 
    }

    active_matches[match_id] = match
    user_matches[str(created_by)] = match_id

    if vs_ai:
        # AI is added as Player 0
        match["players"].append({
            "id": "ai_bot",
            "name": "🤖 APEX AI",
            "is_ai": True
        })

    if str(chat_id) not in group_matches:
        group_matches[str(chat_id)] = []
    group_matches[str(chat_id)].append(match_id)

    return match

def get_match(match_id):
    return active_matches.get(match_id)

def remove_match(match_id):
    match = get_match(match_id)
    if not match: return
    for player in match.get("players", []):
        user_id = str(player.get("id"))
        if user_id in user_matches and user_id != "ai_bot":
            del user_matches[user_id]
    if match_id in active_matches:
        del active_matches[match_id]

def cleanup_expired_matches():
    expired = []
    now = datetime.utcnow()
    for match_id, match in list(active_matches.items()):
        if (now - match["last_activity"]).total_seconds() > MATCH_TIMEOUT:
            expired.append(match_id)
    for match_id in expired: remove_match(match_id)

# ================= GAME LOGIC =================
def get_batter_bowler(match):
    """Determine who is batting and bowling"""
    p1_id = match["players"][0]["id"]
    p2_id = match["players"][1]["id"]
    
    batting_first = match["batting_first"]
    
    if match["inning"] == 1:
        batter = batting_first
        bowler = p2_id if p1_id == batting_first else p1_id
    else:
        bowler = batting_first
        batter = p2_id if p1_id == batting_first else p1_id
        
    return str(batter), str(bowler)

def process_turn(match, bat_val, bowl_val):
    match["last_activity"] = datetime.utcnow()
    
    # --- GAME CONFIG ---
    MAX_WICKETS = 2
    MAX_OVERS = 1
    # -------------------
    
    is_wicket = (bat_val == bowl_val)
    result_text = ""
    commentary = ""
    
    if is_wicket:
        match["wickets"] += 1
        result_text = f"☝️ OUT! {bat_val} vs {bowl_val}"
        commentary = random.choice(["Clean bowled!", "Caught!", "LBW!", "Gone!"])
    else:
        match["score"] += bat_val
        result_text = f"✨ {bat_val} runs! ({bat_val} vs {bowl_val})"
        commentary = random.choice(["Shot!", "Boundary!", "Running hard!", "Smashed!"])

    match["balls"] += 1
    if match["balls"] == 6:
        match["overs"] += 1
        match["balls"] = 0

    state = {
        "result": result_text,
        "commentary": commentary,
        "bat_val": bat_val,
        "bowl_val": bowl_val,
        "next_state": "continue"
    }

    # Win/Innings Logic
    if match["inning"] == 1:
        if match["wickets"] >= MAX_WICKETS or match["overs"] >= MAX_OVERS:
            state["next_state"] = "innings_over"
            match["target"] = match["score"] + 1
            match["inning"] = 2
            match["score"] = 0
            match["wickets"] = 0
            match["overs"] = 0
            match["balls"] = 0
    else:
        # Check if Chasing Team Won
        if match["score"] >= match["target"]:
            state["next_state"] = "match_over"
            state["winner_id"] = get_batter_bowler(match)[0] # Batter wins
        # Check if Defending Team Won
        elif match["wickets"] >= MAX_WICKETS or match["overs"] >= MAX_OVERS:
            if match["score"] >= match["target"]:
                 state["next_state"] = "match_over"
                 state["winner_id"] = get_batter_bowler(match)[0]
            elif match["score"] == match["target"] - 1:
                 state["next_state"] = "match_over"
                 state["winner_id"] = "tie"
            else:
                 state["next_state"] = "match_over"
                 state["winner_id"] = get_batter_bowler(match)[1] # Bowler wins

    return state

# ================= KEYBOARDS =================
def get_game_keyboard(match_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣", callback_data=f"n1_{match_id}"),
         InlineKeyboardButton("2️⃣", callback_data=f"n2_{match_id}"),
         InlineKeyboardButton("3️⃣", callback_data=f"n3_{match_id}")],
        [InlineKeyboardButton("4️⃣", callback_data=f"n4_{match_id}"),
         InlineKeyboardButton("5️⃣", callback_data=f"n5_{match_id}"),
         InlineKeyboardButton("6️⃣", callback_data=f"n6_{match_id}")],
        [InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{match_id}")]
    ])

# ================= COMMANDS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🎮 START PLAYING", callback_data="cricket_menu")]]
    await update.message.reply_text("🏏 *APEX CRICKET BOT*\n\nWelcome! Click below to play.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🤖 VS AI", callback_data="play_ai")],
        [InlineKeyboardButton("👥 VS FRIEND", callback_data="play_friend")]
    ]
    await update.message.reply_text("🎮 *Choose Mode:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# ================= CALLBACKS (THE FIX IS HERE) =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data

    try: await query.answer()
    except: pass

    if data == "cricket_menu":
        kb = [[InlineKeyboardButton("🤖 VS AI", callback_data="play_ai")],
              [InlineKeyboardButton("👥 VS FRIEND", callback_data="play_friend")]]
        await query.edit_message_text("🎮 *Choose Mode:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == "play_ai":
        if str(user.id) in user_matches:
            await query.answer("Already in a match!", show_alert=True)
            return

        match = create_match(query.message.chat_id, user.id, vs_ai=True)
        # Add User (Index 1)
        match["players"].append({"id": str(user.id), "name": user.first_name})
        match["state"] = "toss"
        
        kb = [[InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match['match_id']}"),
               InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match['match_id']}")]]
        
        await query.edit_message_text(
            f"🤖 *AI MATCH STARTED*\nID: `{match['match_id']}`\n\n🪙 *TOSS TIME*\nCall Heads or Tails:", 
            reply_markup=InlineKeyboardMarkup(kb), 
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "play_friend":
        match = create_match(query.message.chat_id, user.id, vs_ai=False)
        match["players"].append({"id": str(user.id), "name": user.first_name})
        kb = [[InlineKeyboardButton("✅ JOIN", callback_data=f"join_{match['match_id']}")]]
        await query.edit_message_text(f"👥 *PUBLIC MATCH*\nID: `{match['match_id']}`\nWaiting for opponent...", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("join_"):
        match_id = data.split("_")[1]
        match = get_match(match_id)
        if not match or len(match["players"]) >= 2:
            await query.answer("Match invalid or full", show_alert=True)
            return
        
        match["players"].append({"id": str(user.id), "name": user.first_name})
        user_matches[str(user.id)] = match_id
        match["state"] = "toss"
        
        kb = [[InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match_id}"),
               InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match_id}")]]
        await query.edit_message_text(f"🎮 *MATCH ON!*\n{match['players'][0]['name']} vs {match['players'][1]['name']}\n\n🪙 {user.first_name} calls Toss:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    # --- TOSS LOGIC FIXED HERE ---
    elif data.startswith(("heads_", "tails_")):
        action, match_id = data.split("_")
        match = get_match(match_id)
        
        if not match: return

        # Validate that the clicker is actually in the match
        if str(user.id) not in [p["id"] for p in match["players"] if p["id"] != "ai_bot"]:
             await query.answer("You are not in this match.", show_alert=True)
             return

        # If PvP, ensure it's the 2nd player's turn to toss (fairness)
        if not match["vs_ai"] and str(user.id) == match["players"][0]["id"]:
             await query.answer("Let the opponent call the toss!", show_alert=True)
             return

        toss_result = random.choice(["heads", "tails"])
        player_won = (action == toss_result)
        
        winner_id = None
        winner_name = ""

        if match["vs_ai"]:
            if player_won:
                winner_id = str(user.id)
                winner_name = user.first_name
            else:
                winner_id = "ai_bot"
                winner_name = "🤖 APEX AI"
        else:
            p1_id = match["players"][0]["id"]
            p2_id = match["players"][1]["id"]
            if player_won:
                winner_id = str(user.id)
                winner_name = user.first_name
            else:
                # If caller lost, the other player won
                winner_id = p1_id if str(user.id) == p2_id else p2_id
                winner_name = match["players"][0]["name"] if str(user.id) == p2_id else match["players"][1]["name"]

        match["toss_winner"] = winner_id
        match["batting_first"] = winner_id # Winner always bats first
        match["state"] = "playing"

        await query.edit_message_text(
            f"🪙 Result: *{toss_result.upper()}*\n🏆 Winner: *{winner_name}*\n🏏 Batting First: *{winner_name}*\n\n⚡ Match Starting...",
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(2)
        await update_game_board(query.message, match)

    # --- GAMEPLAY ---
    elif data.startswith("n"):
        parts = data.split("_")
        choice = int(parts[0][1])
        match_id = parts[1]
        match = get_match(match_id)
        
        if not match: return

        if match["vs_ai"]:
            ai_move = match["ai_bot"].make_move()
            batter_id, _ = get_batter_bowler(match)
            
            # Identify roles
            if str(user.id) == batter_id:
                res = process_turn(match, choice, ai_move)
            else:
                res = process_turn(match, ai_move, choice)
                
            await handle_turn_result(query.message, match, res)
        
        else:
            # PvP Sync
            match["current_choices"][str(user.id)] = choice
            if len(match["current_choices"]) == 2:
                bat_id, bowl_id = get_batter_bowler(match)
                res = process_turn(match, match["current_choices"][bat_id], match["current_choices"][bowl_id])
                match["current_choices"] = {}
                await handle_turn_result(query.message, match, res)
            else:
                await query.edit_message_text("✅ You moved. Waiting for opponent...", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("surrender_"):
        match_id = data.split("_")[1]
        match = get_match(match_id)
        if match: await end_match_display(query.message, match, "surrender", user.first_name)

# ================= UI UPDATERS =================
async def update_game_board(message, match):
    bat_id, bowl_id = get_batter_bowler(match)
    
    # Get Names safely
    def get_name(pid):
        if pid == "ai_bot": return "APEX AI"
        for p in match["players"]:
            if p["id"] == pid: return p["name"]
        return "Unknown"

    target_msg = f"Target: {match['target']}" if match['target'] else "1st Innings"

    text = (
        f"🏏 *{get_name(bat_id)}* (Bat) vs ⚾ *{get_name(bowl_id)}* (Bowl)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score: *{match['score']} - {match['wickets']}*\n"
        f"🎯 {target_msg} | 📦 {match['overs']}.{match['balls']} ov\n"
        f"👇 *Choose your move:*"
    )
    try: await message.edit_text(text, reply_markup=get_game_keyboard(match["match_id"]), parse_mode=ParseMode.MARKDOWN)
    except: pass

async def handle_turn_result(message, match, result):
    text = (
        f"🎲 *RESULT:*\n"
        f"🏏 Bat: {result['bat_val']} | ⚾ Bowl: {result['bowl_val']}\n\n"
        f"{result['result']}\n"
        f"🗣️ {result['commentary']}"
    )
    try: await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except: pass
    
    await asyncio.sleep(2.5)

    if result["next_state"] == "continue":
        await update_game_board(message, match)
    elif result["next_state"] == "innings_over":
        await message.edit_text(f"🔄 *INNINGS BREAK*\nTarget: {match['target']}", parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(2)
        await update_game_board(message, match)
    elif result["next_state"] == "match_over":
        await end_match_display(message, match, result["winner_id"])

async def end_match_display(message, match, winner_id, surrenderer=None):
    if winner_id == "surrender":
        res = f"🏳️ {surrenderer} Surrendered!"
    elif winner_id == "tie":
        res = "🤝 Match Tied!"
    else:
        name = "🤖 APEX AI" if winner_id == "ai_bot" else "Unknown"
        for p in match["players"]:
            if p["id"] == winner_id: name = p["name"]
        res = f"🏆 Winner: {name}"

    kb = [[InlineKeyboardButton("🎮 PLAY AGAIN", callback_data="cricket_menu")]]
    await message.edit_text(f"🏁 *MATCH OVER*\n\n{res}\nFinal Score: {match['score']}/{match['wickets']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    remove_match(match["match_id"])

# ================= RUN =================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("play", menu_command))
    application.add_handler(CommandHandler("cricket", menu_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    asyncio.create_task(cleanup_task())
    
    print("✅ BOT STARTED")
    application.run_polling()
