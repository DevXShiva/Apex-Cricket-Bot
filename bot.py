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
ADMIN_ID = 5298223577

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
    # Run without debug/reloader to prevent main thread interference
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ================= IN-MEMORY STORAGE =================
active_matches = {}
user_matches = {}
group_matches = defaultdict(list)
private_invites = {}

MATCH_TIMEOUT = 300  # Increased to 5 minutes for better gameplay
MAX_MATCHES_PER_GROUP = 10

# ================= AI BOT SYSTEM =================
class AICricketBot:
    """Standard AI Opponent"""
    def __init__(self):
        self.name = "🤖 APEX AI"
        self.id = "ai_bot"

    def make_move(self, opponent_move=None):
        """AI makes a move (1-6). Slightly smart randomization."""
        # Weighted random to make it feel more 'real'
        weights = [10, 20, 15, 20, 10, 25] # Slightly higher chance for 2, 4, 6
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
        "is_private": is_private,
        "invited_user": invited_user,
        "players": [],
        "state": "waiting",
        # Scoreboard
        "score": 0,
        "wickets": 0,
        "overs": 0,
        "balls": 0,
        "target": None,
        # Meta
        "created_at": datetime.utcnow(),
        "last_activity": datetime.utcnow(),
        "message_id": None,
        # Game State
        "toss_winner": None,
        "batting_first": None, # Store Player ID
        "inning": 1,
        "current_choices": {} # Stores {player_id: choice} for sync
    }

    active_matches[match_id] = match
    user_matches[str(created_by)] = match_id

    if vs_ai:
        match["players"].append({
            "id": "ai_bot",
            "name": "🤖 APEX AI",
            "is_ai": True
        })

    if str(chat_id) not in group_matches:
        group_matches[str(chat_id)] = []
    group_matches[str(chat_id)].append(match_id)

    if is_private and invited_user:
        private_invites[str(invited_user)] = match_id

    return match

def get_match(match_id):
    return active_matches.get(match_id)

def get_user_match(user_id):
    match_id = user_matches.get(str(user_id))
    return active_matches.get(match_id) if match_id else None

def remove_match(match_id):
    match = get_match(match_id)
    if not match:
        return

    # Cleanup user mappings
    for player in match.get("players", []):
        user_id = str(player.get("id"))
        if user_id in user_matches and user_id != "ai_bot":
            del user_matches[user_id]

    # Cleanup group mappings
    chat_id = match.get("chat_id")
    if chat_id in group_matches and match_id in group_matches[chat_id]:
        group_matches[chat_id].remove(match_id)

    # Cleanup active list
    if match_id in active_matches:
        del active_matches[match_id]

def cleanup_expired_matches():
    expired = []
    now = datetime.utcnow()
    for match_id, match in list(active_matches.items()):
        time_diff = (now - match["last_activity"]).total_seconds()
        if time_diff > MATCH_TIMEOUT:
            expired.append(match_id)
    
    for match_id in expired:
        remove_match(match_id)

# ================= GAME LOGIC HELPER =================
def get_batter_bowler(match):
    """Determine current batter and bowler IDs"""
    p1 = match["players"][0]["id"]
    p2 = match["players"][1]["id"]
    
    batting_first = match["batting_first"]
    
    if match["inning"] == 1:
        batter = batting_first
        bowler = p2 if p1 == batting_first else p1
    else:
        # Swap for 2nd inning
        bowler = batting_first
        batter = p2 if p1 == batting_first else p1
        
    return str(batter), str(bowler)

def process_turn(match, bat_val, bowl_val):
    match["last_activity"] = datetime.utcnow()
    
    # Logic: 1 wicket hand cricket for speed, or 2 wickets as per original text
    MAX_WICKETS = 2
    MAX_OVERS = 1
    
    is_wicket = (bat_val == bowl_val)
    
    result_text = ""
    commentary = ""
    
    if is_wicket:
        match["wickets"] += 1
        result_text = f"☝️ OUT! {bat_val} vs {bowl_val}"
        commentary = random.choice(["Clean bowled!", "Caught out!", "LBW!", "Stumped!"])
    else:
        match["score"] += bat_val
        result_text = f"✨ {bat_val} runs! ({bat_val} vs {bowl_val})"
        commentary = random.choice(["Great shot!", "Boundary!", "Good running!", "Smashed it!"])

    match["balls"] += 1
    if match["balls"] == 6:
        match["overs"] += 1
        match["balls"] = 0

    # Status Checks
    game_state = {
        "result": result_text,
        "commentary": commentary,
        "bat_val": bat_val,
        "bowl_val": bowl_val,
        "next_state": "continue" # continue, innings_over, match_over
    }

    # Check Innings End Conditions
    inning_over = False
    
    if match["inning"] == 1:
        if match["wickets"] >= MAX_WICKETS or match["overs"] >= MAX_OVERS:
            inning_over = True
            match["target"] = match["score"] + 1
            game_state["next_state"] = "innings_over"
            
            # Setup Innings 2
            match["inning"] = 2
            match["score"] = 0
            match["wickets"] = 0
            match["overs"] = 0
            match["balls"] = 0
            
    else: # Innings 2
        if match["score"] >= match["target"]:
            game_state["next_state"] = "match_over"
            game_state["winner_id"] = get_batter_bowler(match)[0] # Current Batter wins
        elif match["wickets"] >= MAX_WICKETS or match["overs"] >= MAX_OVERS:
            if match["score"] >= match["target"]:
                 game_state["next_state"] = "match_over"
                 game_state["winner_id"] = get_batter_bowler(match)[0]
            elif match["score"] == match["target"] - 1: # Tie
                 game_state["next_state"] = "match_over"
                 game_state["winner_id"] = "tie"
            else:
                 game_state["next_state"] = "match_over"
                 game_state["winner_id"] = get_batter_bowler(match)[1] # Current Bowler wins

    return game_state

# ================= KEYBOARDS =================
def get_game_keyboard(match_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1️⃣", callback_data=f"n1_{match_id}"),
            InlineKeyboardButton("2️⃣", callback_data=f"n2_{match_id}"),
            InlineKeyboardButton("3️⃣", callback_data=f"n3_{match_id}")
        ],
        [
            InlineKeyboardButton("4️⃣", callback_data=f"n4_{match_id}"),
            InlineKeyboardButton("5️⃣", callback_data=f"n5_{match_id}"),
            InlineKeyboardButton("6️⃣", callback_data=f"n6_{match_id}")
        ],
        [InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{match_id}")]
    ])

# ================= COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🏏 *APEX CRICKET BOT*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Play Hand Cricket against AI or Friends!\n\n"
        "🚀 *Commands:*\n"
        "/play - Start a match\n"
        "/join [ID] - Join a match\n"
        "/matches - List active matches\n"
        "/cancel - Cancel current match\n\n"
        "👇 Click below to start:"
    )
    keyboard = [[InlineKeyboardButton("🎮 START PLAYING", callback_data="cricket_menu")]]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def cricket_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) in user_matches:
        match_id = user_matches[str(user.id)]
        await update.message.reply_text(f"⚠️ You are already in match `{match_id}`. Finish it or use /cancel.", parse_mode=ParseMode.MARKDOWN)
        return

    text = (
        "🎮 *CHOOSE MODE*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 *VS AI*: Quick match against the bot\n"
        "👥 *VS FRIEND*: Public match for anyone in group\n"
        "🔒 *PRIVATE*: Challenge specific user"
    )
    keyboard = [
        [InlineKeyboardButton("🤖 VS AI", callback_data="play_ai")],
        [InlineKeyboardButton("👥 VS FRIEND", callback_data="play_friend"),
         InlineKeyboardButton("🔒 PRIVATE", callback_data="challenge_menu")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# ================= CALLBACK HANDLERS (CORE LOGIC) =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data

    try:
        # Always answer to stop loading animation
        await query.answer()
    except:
        pass

    # --- Navigation ---
    if data == "cricket_menu":
        await query.edit_message_text(
            "🎮 *CHOOSE MODE*\n━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 VS AI", callback_data="play_ai")],
                [InlineKeyboardButton("👥 VS FRIEND", callback_data="play_friend"),
                 InlineKeyboardButton("🔒 PRIVATE", callback_data="challenge_menu")]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

    elif data == "challenge_menu":
        await query.edit_message_text(
            "🔒 *PRIVATE CHALLENGE*\n\n"
            "To challenge someone, reply to their message or type:\n"
            "`/challenge @username`",
            parse_mode=ParseMode.MARKDOWN
        )

    # --- Start AI Match ---
    elif data == "play_ai":
        if str(user.id) in user_matches:
            await query.answer("You are already in a match!", show_alert=True)
            return

        match = create_match(query.message.chat_id, user.id, vs_ai=True)
        if not match:
            await query.answer("Error creating match.", show_alert=True)
            return

        # Add User
        match["players"].append({"id": str(user.id), "name": user.first_name})
        match["state"] = "toss"
        match["message_id"] = query.message.message_id

        text = (
            f"🤖 *AI MATCH STARTED*\n"
            f"ID: `{match['match_id']}`\n\n"
            f"🪙 *TOSS TIME*\n"
            f"Call Heads or Tails:"
        )
        keyboard = [
            [InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match['match_id']}"),
             InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match['match_id']}")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # --- Start Friend Match ---
    elif data == "play_friend":
        if str(user.id) in user_matches:
            await query.answer("You are already in a match!", show_alert=True)
            return
        
        match = create_match(query.message.chat_id, user.id, vs_ai=False)
        match["players"].append({"id": str(user.id), "name": user.first_name})
        match["message_id"] = query.message.message_id
        
        text = (
            f"👥 *PUBLIC MATCH OPEN*\n"
            f"ID: `{match['match_id']}`\n"
            f"Player 1: {user.first_name}\n\n"
            f"Waiting for opponent..."
        )
        keyboard = [[InlineKeyboardButton("✅ JOIN GAME", callback_data=f"join_{match['match_id']}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # --- Join Match ---
    elif data.startswith("join_"):
        match_id = data.split("_")[1]
        match = get_match(match_id)
        
        if not match:
            await query.answer("Match expired or not found.", show_alert=True)
            return
            
        if str(user.id) in user_matches:
            await query.answer("You are already in a match!", show_alert=True)
            return
            
        if len(match["players"]) >= 2:
            await query.answer("Match is full!", show_alert=True)
            return

        # Add P2
        match["players"].append({"id": str(user.id), "name": user.first_name})
        user_matches[str(user.id)] = match_id
        match["state"] = "toss"
        
        text = (
            f"🎮 *MATCH STARTED*\n"
            f"P1: {match['players'][0]['name']}\n"
            f"P2: {match['players'][1]['name']}\n\n"
            f"🪙 *TOSS:* {user.first_name} calls it!"
        )
        keyboard = [
            [InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match_id}"),
             InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match_id}")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    # --- Toss Logic ---
    elif data.startswith(("heads_", "tails_")):
        action, match_id = data.split("_")
        match = get_match(match_id)
        
        if not match or match["state"] != "toss":
            await query.answer("Invalid state.", show_alert=True)
            return

        # Only the joining player (or P1 vs AI) should click toss
        allowed_clicker = match["players"][0]["id"] if match["vs_ai"] else match["players"][1]["id"]
        
        if str(user.id) != allowed_clicker:
            await query.answer("Not your turn to call toss!", show_alert=True)
            return

        toss_outcome = random.choice(["heads", "tails"])
        did_win = (action == toss_outcome)
        
        winner_id = str(user.id) if did_win else (match["players"][0]["id"] if str(user.id) == match["players"][1]["id"] else match["players"][1]["id"])
        
        if match["vs_ai"] and not did_win:
             winner_id = "ai_bot"

        match["toss_winner"] = winner_id
        match["state"] = "playing"
        
        # Decide batting (Winner always bats first for simplicity to speed up flow)
        match["batting_first"] = winner_id
        
        winner_name = "APEX AI" if winner_id == "ai_bot" else \
                      next(p["name"] for p in match["players"] if p["id"] == winner_id)

        text = (
            f"🪙 Result: *{toss_outcome.upper()}*\n"
            f"🏆 Winner: {winner_name}\n"
            f"🏏 {winner_name} elected to BAT first!\n\n"
            f"⚡ *MATCH STARTING...*"
        )
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        await asyncio.sleep(2)
        await update_game_board(query.message, match)

    # --- Game Input (1-6) ---
    elif data.startswith("n"):
        parts = data.split("_") # n1_MATCHID
        choice = int(parts[0][1])
        match_id = parts[1]
        match = get_match(match_id)

        if not match:
            await query.answer("Match not found", show_alert=True)
            return

        # Check if user is in match
        if str(user.id) not in [p["id"] for p in match["players"]]:
            await query.answer("You are not in this match.", show_alert=True)
            return

        # --- AI Match Logic ---
        if match["vs_ai"]:
            # AI plays immediately
            ai_choice = match["ai_bot"].make_move()
            
            batter, bowler = get_batter_bowler(match)
            if str(user.id) == batter:
                result = process_turn(match, choice, ai_choice)
            else:
                result = process_turn(match, ai_choice, choice)
            
            await handle_turn_result(query.message, match, result)

        # --- PvP Logic (Sync) ---
        else:
            # Store choice
            match["current_choices"][str(user.id)] = choice
            
            # Check if both played
            if len(match["current_choices"]) == 2:
                batter_id, bowler_id = get_batter_bowler(match)
                
                bat_val = match["current_choices"][batter_id]
                bowl_val = match["current_choices"][bowler_id]
                
                # Clear choices for next ball
                match["current_choices"] = {}
                
                result = process_turn(match, bat_val, bowl_val)
                await handle_turn_result(query.message, match, result)
            else:
                # Wait for opponent
                opponent = next(p["name"] for p in match["players"] if p["id"] != str(user.id))
                try:
                    await query.edit_message_text(
                        f"✅ You chose: *{choice}*\n"
                        f"⏳ Waiting for {opponent}...",
                        reply_markup=None, # Remove buttons to prevent double click
                        parse_mode=ParseMode.MARKDOWN
                    )
                except:
                    pass

    elif data.startswith("surrender_"):
        match_id = data.split("_")[1]
        match = get_match(match_id)
        if match:
             await end_match_display(query.message, match, winner_id="surrender", surrenderer_name=user.first_name)


# ================= GAME UI UPDATES =================
async def update_game_board(message, match):
    batter_id, bowler_id = get_batter_bowler(match)
    
    # Get Names
    p1 = match["players"][0]
    p2 = match["players"][1] if len(match["players"]) > 1 else None
    
    batter_name = p1["name"] if p1["id"] == batter_id else (p2["name"] if p2 and p2["id"] == batter_id else "AI")
    bowler_name = p1["name"] if p1["id"] == bowler_id else (p2["name"] if p2 and p2["id"] == bowler_id else "AI")

    target_txt = f"/ {match['target']}" if match['target'] else ""
    
    text = (
        f"🏏 *MATCH IN PROGRESS*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏏 Bat: *{batter_name}*\n"
        f"⚾ Bowl: *{bowler_name}*\n\n"
        f"📊 Score: *{match['score']} - {match['wickets']}* {target_txt}\n"
        f"📦 Overs: *{match['overs']}.{match['balls']}*\n\n"
        f"👇 Select your number:"
    )
    
    try:
        await message.edit_text(text, reply_markup=get_game_keyboard(match["match_id"]), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"UI Update error: {e}")

async def handle_turn_result(message, match, result):
    # Determine names for display
    batter_id, bowler_id = get_batter_bowler(match)
    # Note: result has next_state info
    
    # Simple animation text
    text = (
        f"🎲 *BALL RESULT*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏏 Bat: {result['bat_val']} | ⚾ Bowl: {result['bowl_val']}\n\n"
        f"{result['result']}\n"
        f"🗣️ {result['commentary']}"
    )
    
    try:
        await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except:
        pass
        
    await asyncio.sleep(2) # Short pause to read result

    if result["next_state"] == "continue":
        await update_game_board(message, match)
        
    elif result["next_state"] == "innings_over":
        text = (
            f"🔄 *INNINGS BREAK*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Score: {match['score']}/{match['wickets']}\n"
            f"🎯 Target: *{match['target']}*\n\n"
            f"Starting 2nd Innings..."
        )
        try:
            await message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        await asyncio.sleep(3)
        await update_game_board(message, match)
        
    elif result["next_state"] == "match_over":
        await end_match_display(message, match, winner_id=result["winner_id"])

async def end_match_display(message, match, winner_id, surrenderer_name=None):
    if winner_id == "surrender":
        res_text = f"🏳️ {surrenderer_name} Surrendered!"
    elif winner_id == "tie":
        res_text = "🤝 It's a TIE!"
    elif winner_id == "ai_bot":
        res_text = "🏆 Winner: 🤖 APEX AI"
    else:
        # Find user name
        w_name = next((p["name"] for p in match["players"] if p["id"] == winner_id), "Unknown")
        res_text = f"🏆 Winner: {w_name}"

    text = (
        f"🏁 *MATCH ENDED*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{res_text}\n\n"
        f"Final Score: {match['score']}/{match['wickets']}\n"
        f"Overs: {match['overs']}.{match['balls']}\n\n"
        f"🎮 Use /play to start again!"
    )
    
    remove_match(match["match_id"])
    
    try:
        # Only show Play Menu button
        kb = [[InlineKeyboardButton("🎮 PLAY AGAIN", callback_data="cricket_menu")]]
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    except:
        pass

# ================= HELPER COMMANDS =================
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) not in user_matches:
        await update.message.reply_text("You are not in a match.")
        return
        
    match_id = user_matches[str(user.id)]
    remove_match(match_id)
    await update.message.reply_text("✅ Match cancelled.")

async def cleanup_task():
    while True:
        await asyncio.sleep(60)
        cleanup_expired_matches()

# ================= MAIN =================
def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN env variable missing!")
        return

    # Start Flask in separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Telegram App
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("play", cricket_menu_command))
    application.add_handler(CommandHandler("cricket", cricket_menu_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Async Cleanup
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    print("✅ APEX CRICKET BOT STARTED")
    
    # Run Polling (Blocking)
    application.run_polling()

if __name__ == "__main__":
    main()
