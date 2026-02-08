import os
import json
import random
import asyncio
import uuid
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import logging
from collections import defaultdict
import threading

import pymongo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode, ChatType
from flask import Flask, jsonify

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
ADMIN_ID = 5298223577

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask App
app = Flask(__name__)
@app.route('/')
def home(): return jsonify({"status": "online", "service": "APEX Cricket Bot"})
@app.route('/health')
def health(): return jsonify({"status": "healthy"})
@app.route('/ping')
def ping(): return "pong"

# In-memory storage
active_matches = {}
user_matches = {}
group_matches = defaultdict(list)
private_invites = {}
ai_matches = {}  # Store AI vs Human matches

MATCH_TIMEOUT = 120
MAX_MATCHES_PER_GROUP = 10

# ================= COLORED BUTTONS SYSTEM =================
def create_button(text, callback_data=None, url=None, style=None, emoji_id=None):
    """Create button with color and style"""
    button_dict = {"text": text}
    if callback_data: button_dict["callback_data"] = callback_data
    if url: button_dict["url"] = url
    if style: button_dict["style"] = style
    if emoji_id: button_dict["icon_custom_emoji_id"] = emoji_id
    return button_dict

def primary_btn(text, callback_data):
    return create_button(text, callback_data, style="primary")

def success_btn(text, callback_data):
    return create_button(text, callback_data, style="success")

def danger_btn(text, callback_data):
    return create_button(text, callback_data, style="danger")

def normal_btn(text, callback_data):
    return create_button(text, callback_data)

# ================= AI BOT SYSTEM =================
class AICricketBot:
    """AI opponent with different difficulty levels"""
    
    def __init__(self, difficulty="medium"):
        self.difficulty = difficulty
        self.name = "🤖 APEX AI"
        self.id = "ai_bot"
        self.patterns = []
        self.last_moves = []
    
    def make_move(self, match_state=None):
        """AI makes a cricket move (1-6) based on difficulty"""
        if self.difficulty == "easy":
            # Easy AI - more predictable
            if random.random() < 0.6:  # 60% chance of 1-3
                return random.randint(1, 3)
            return random.randint(1, 6)
        
        elif self.difficulty == "medium":
            # Medium AI - balanced
            weights = [15, 20, 25, 20, 15, 5]  # Weighted probabilities
            return random.choices(range(1, 7), weights=weights)[0]
        
        elif self.difficulty == "hard":
            # Hard AI - strategic
            if match_state:
                # Analyze match state
                if match_state.get("wickets", 0) >= 1:
                    return random.randint(1, 3)  # Defensive
                elif match_state.get("balls", 0) >= 3:
                    return random.randint(4, 6)  # Aggressive
            return random.randint(1, 6)
        
        else:
            return random.randint(1, 6)
    
    def get_commentary(self, runs, is_wicket=False):
        """AI commentary"""
        if is_wicket:
            comments = [
                "🤖 AI: Perfect delivery! Wicket!",
                "🤖 AI: Got you! That's out!",
                "🤖 AI: Brilliant ball! Clean bowled!",
                "🤖 AI: Calculated! Wicket secured!"
            ]
        elif runs == 6:
            comments = [
                "🤖 AI: Maximum! Perfect shot!",
                "🤖 AI: Six! That's huge!",
                "🤖 AI: Over the ropes! Beautiful!",
                "🤖 AI: AI smashes it for six!"
            ]
        elif runs == 4:
            comments = [
                "🤖 AI: Boundary! Well played!",
                "🤖 AI: Four runs! Nice shot!",
                "🤖 AI: Edge to boundary!",
                "🤖 AI: AI finds the gap!"
            ]
        else:
            comments = [
                f"🤖 AI: {runs} runs taken.",
                f"🤖 AI: Good running, {runs} runs.",
                f"🤖 AI: Takes {runs} runs.",
                f"🤖 AI: {runs} runs added."
            ]
        return random.choice(comments)

# ================= MATCH MANAGEMENT =================
def create_match(chat_id, created_by, is_private=False, invited_user=None, vs_ai=False, ai_difficulty="medium"):
    if len(group_matches.get(str(chat_id), [])) >= MAX_MATCHES_PER_GROUP:
        return None
    
    match_id = str(uuid.uuid4())[:6].upper()
    
    match = {
        "match_id": match_id,
        "chat_id": str(chat_id),
        "created_by": str(created_by),
        "is_private": is_private,
        "invited_user": invited_user,
        "vs_ai": vs_ai,
        "ai_difficulty": ai_difficulty,
        "ai_bot": AICricketBot(ai_difficulty) if vs_ai else None,
        "players": [],
        "state": "waiting",
        "score": 0,
        "wickets": 0,
        "overs": 0,
        "balls": 0,
        "target": None,
        "created_at": datetime.utcnow(),
        "last_activity": datetime.utcnow(),
        "message_id": None,
        "current_batsman": None,
        "current_bowler": None,
        "choices": {},
        "ball_history": [],
        "inning": 1,
        "max_overs": 1,
        "max_wickets": 2
    }
    
    active_matches[match_id] = match
    user_matches[str(created_by)] = match_id
    
    if vs_ai:
        ai_matches[match_id] = match
        # AI joins automatically
        match["players"].append({
            "id": "ai_bot",
            "name": "🤖 APEX AI",
            "username": "apex_ai",
            "is_ai": True
        })
    
    if str(chat_id) not in group_matches:
        group_matches[str(chat_id)] = []
    group_matches[str(chat_id)].append(match_id)
    
    if is_private and invited_user:
        private_invites[str(invited_user)] = match_id
    
    logger.info(f"🎮 Match created: {match_id} {'vs AI' if vs_ai else ''}")
    return match

def get_match(match_id):
    return active_matches.get(match_id)

def get_user_match(user_id):
    match_id = user_matches.get(str(user_id))
    if match_id:
        return active_matches.get(match_id)
    return None

def join_match(match_id, user_data):
    match = get_match(match_id)
    if not match:
        return False
    
    if len(match["players"]) >= 2:
        return False
    
    if match["is_private"] and match["invited_user"]:
        if user_data["username"] and user_data["username"].lower() != match["invited_user"].lower():
            return False
    
    if str(user_data["id"]) in user_matches:
        return False
    
    match["players"].append(user_data)
    match["last_activity"] = datetime.utcnow()
    user_matches[str(user_data["id"])] = match_id
    
    logger.info(f"👤 Player {user_data['id']} joined match {match_id}")
    return True

def remove_match(match_id):
    match = get_match(match_id)
    if not match:
        return
    
    for player in match.get("players", []):
        user_id = str(player.get("id"))
        if user_id in user_matches and user_id != "ai_bot":
            del user_matches[user_id]
    
    chat_id = match.get("chat_id")
    if chat_id in group_matches and match_id in group_matches[chat_id]:
        group_matches[chat_id].remove(match_id)
    
    for user_id, mid in list(private_invites.items()):
        if mid == match_id:
            del private_invites[user_id]
    
    if match_id in active_matches:
        del active_matches[match_id]
    
    if match_id in ai_matches:
        del ai_matches[match_id]
    
    logger.info(f"🗑️ Match removed: {match_id}")

def cleanup_expired_matches():
    expired = []
    now = datetime.utcnow()
    
    for match_id, match in list(active_matches.items()):
        time_diff = (now - match["last_activity"]).total_seconds()
        if time_diff > MATCH_TIMEOUT:
            expired.append(match_id)
    
    for match_id in expired:
        logger.info(f"🧹 Cleaning expired match: {match_id}")
        remove_match(match_id)
    
    return len(expired)

# ================= CRICKET GAME LOGIC =================
async def play_ball(match_id, batsman_choice):
    """Process a ball in the match"""
    match = get_match(match_id)
    if not match:
        return None
    
    match["last_activity"] = datetime.utcnow()
    
    # Get bowler choice (AI or human)
    bowler_choice = None
    if match["vs_ai"] and match["ai_bot"]:
        # AI makes move
        bowler_choice = match["ai_bot"].make_move(match)
        match["choices"]["ai_bot"] = bowler_choice
    else:
        # Wait for human bowler choice
        # This would be handled in callback
        return None
    
    match["choices"][match["current_batsman"]] = batsman_choice
    
    # Process ball
    is_wicket = (batsman_choice == bowler_choice)
    
    if is_wicket:
        match["wickets"] += 1
        match["ball_history"].append("W")
        result = f"☝️ OUT! {batsman_choice} = {bowler_choice}"
        commentary = "🎯 WICKET! Bowler strikes!"
    else:
        match["score"] += batsman_choice
        match["ball_history"].append(str(batsman_choice))
        result = f"✨ {batsman_choice} runs! {batsman_choice} ≠ {bowler_choice}"
        commentary = f"🏏 {batsman_choice} runs scored!"
    
    match["balls"] += 1
    if match["balls"] == 6:
        match["overs"] += 1
        match["balls"] = 0
    
    # Check match conditions
    match_completed = False
    winner = None
    
    if match["wickets"] >= match["max_wickets"]:
        match_completed = True
        winner = "bowler"
    elif match["overs"] >= match["max_overs"]:
        match_completed = True
        if match["inning"] == 1:
            # Set target and switch innings
            match["target"] = match["score"] + 1
            match["inning"] = 2
            match["score"] = 0
            match["wickets"] = 0
            match["overs"] = 0
            match["balls"] = 0
            match["ball_history"] = []
            match_completed = False
        else:
            # Second innings complete
            if match["score"] >= match["target"]:
                winner = "batsman"
            else:
                winner = "bowler"
    
    if match["inning"] == 2 and match["target"] and match["score"] >= match["target"]:
        match_completed = True
        winner = "batsman"
    
    return {
        "result": result,
        "commentary": commentary,
        "is_wicket": is_wicket,
        "runs": batsman_choice if not is_wicket else 0,
        "match_completed": match_completed,
        "winner": winner,
        "score": f"{match['score']}/{match['wickets']}",
        "overs": f"{match['overs']}.{match['balls']}"
    }

# ================= COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    welcome_text = (
        "🏏 APEX CRICKET BOT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome to the ultimate hand cricket experience!\n\n"
        "🎮 *NEW FEATURES:*\n"
        "• Play vs 🤖 APEX AI Bot\n"
        "• Multiple matches in same group\n"
        "• Private challenges with friends\n"
        "• Auto-cleanup after 2 mins\n\n"
        "🚀 *QUICK COMMANDS:*\n"
        "/cricket - Start a match\n"
        "/challenge @username - Challenge friend\n"
        "/join MATCHID - Join match\n"
        "/matches - View active matches\n"
        "/cancel - Cancel your match\n"
        "/stats - View statistics\n\n"
        f"⚡ Up to {MAX_MATCHES_PER_GROUP} matches simultaneously!"
    )
    
    keyboard = [
        [primary_btn("🎮 PLAY CRICKET", "play_cricket"), success_btn("📊 MY STATS", "my_stats")],
        [normal_btn("👥 VS FRIEND", "play_friend"), normal_btn("🤖 VS AI", "play_vs_ai")]
    ]
    
    await update.message.reply_text(
        welcome_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )

async def cricket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cricket command - Main entry point"""
    user = update.effective_user
    chat = update.effective_chat
    
    if str(user.id) in user_matches:
        match = get_user_match(user.id)
        await update.message.reply_text(
            f"You are already in a match!\n\n"
            f"Match ID: `{match['match_id']}`\n"
            f"Status: {match['state']}\n\n"
            f"Use `/cancel` to leave current match first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Show match type selection
    match_type_text = (
        "🎮 *CHOOSE MATCH TYPE*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Select how you want to play:\n\n"
        "1. **🤖 VS AI** - Play against APEX AI Bot\n"
        "2. **👥 VS FRIEND** - Challenge a friend\n"
        "3. **🎯 QUICK PLAY** - Public match (anyone can join)\n\n"
        "Click buttons below or use commands:\n"
        "• `/cricket ai` - Play vs AI\n"
        "• `/challenge @username` - Challenge friend"
    )
    
    keyboard = [
        [primary_btn("🤖 PLAY VS AI", "play_ai_menu"), success_btn("👥 VS FRIEND", "play_friend_menu")],
        [normal_btn("🎯 QUICK PLAY", "quick_play"), danger_btn("❌ CANCEL", "cancel_menu")]
    ]
    
    await update.message.reply_text(
        match_type_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )

async def play_vs_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle playing vs AI"""
    user = update.effective_user
    chat = update.effective_chat
    
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check group limit (for group matches)
    if chat.type != ChatType.PRIVATE:
        if len(group_matches.get(str(chat.id), [])) >= MAX_MATCHES_PER_GROUP:
            await update.message.reply_text(
                f"Maximum matches reached in this group! Only {MAX_MATCHES_PER_GROUP} matches can run simultaneously.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    # Show AI difficulty selection
    ai_text = (
        "🤖 *CHOOSE AI DIFFICULTY*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Select AI opponent difficulty:\n\n"
        "🟢 **EASY** - Perfect for beginners\n"
        "🟡 **MEDIUM** - Balanced challenge\n"
        "🔴 **HARD** - For experienced players\n"
        "💀 **EXPERT** - Ultimate challenge!\n\n"
        "Match format: 1 Over | 2 Wickets Max"
    )
    
    keyboard = [
        [success_btn("🟢 EASY", "ai_easy"), normal_btn("🟡 MEDIUM", "ai_medium")],
        [danger_btn("🔴 HARD", "ai_hard"), primary_btn("💀 EXPERT", "ai_expert")],
        [danger_btn("❌ BACK", "back_to_main")]
    ]
    
    await update.message.reply_text(
        ai_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )

async def create_ai_match(user, chat, difficulty="medium"):
    """Create AI match helper"""
    match = create_match(
        chat_id=chat.id,
        created_by=user.id,
        vs_ai=True,
        ai_difficulty=difficulty
    )
    
    if not match:
        return None
    
    # Add human player
    match["players"].append({
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username,
        "is_ai": False
    })
    
    # Start match immediately
    match["state"] = "toss"
    
    # Human calls toss in AI matches
    match_text = (
        f"🤖 *AI MATCH STARTED!*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match['match_id']}`\n"
        f"Player: {user.first_name}\n"
        f"AI Opponent: APEX AI ({difficulty.upper()})\n"
        f"Format: 1 Over | 2 Wickets\n\n"
        f"🎯 *TOSS TIME!*\n"
        f"Call heads or tails:"
    )
    
    keyboard = [
        [normal_btn("🌕 HEADS", f"heads_{match['match_id']}"), 
         normal_btn("🌑 TAILS", f"tails_{match['match_id']}")]
    ]
    
    message = await context.bot.send_message(
        chat_id=chat.id,
        text=match_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["message_id"] = message.message_id
    return match

async def play_friend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle playing vs friend (public match)"""
    user = update.effective_user
    chat = update.effective_chat
    
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if len(group_matches.get(str(chat.id), [])) >= MAX_MATCHES_PER_GROUP:
        await update.message.reply_text(
            f"Maximum matches reached in this group! Only {MAX_MATCHES_PER_GROUP} matches can run simultaneously.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    match = create_match(chat.id, user.id)
    if not match:
        await update.message.reply_text(
            "Could not create match! Try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    match["players"].append({
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    })
    
    match_text = (
        f"🎮 *MATCH CREATED!*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match['match_id']}`\n"
        f"Created by: {user.first_name}\n"
        f"Type: Public Match\n"
        f"⏳ Timeout: 2 minutes\n\n"
        f"🔹 *Players (1/2):*\n"
        f"1. {user.first_name}\n"
        f"2. Waiting for opponent...\n\n"
        f"🎯 *To join:* `/join {match['match_id']}`\n\n"
        f"⏰ Match auto-cancels in 2 minutes if no one joins."
    )
    
    keyboard = [
        [success_btn(f"✅ JOIN {match['match_id']}", f"join_{match['match_id']}"), 
         danger_btn("❌ CANCEL", f"cancel_{match['match_id']}")],
        [normal_btn("🤖 VS AI INSTEAD", f"switch_to_ai_{match['match_id']}")]
    ]
    
    message = await update.message.reply_text(
        match_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["message_id"] = message.message_id

async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "Usage: `/challenge @username`\n\n"
            "Example: `/challenge @john`\n\n"
            "Only that user can join your match!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    username = args[0]
    if username.startswith('@'):
        username = username[1:]
    
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if len(group_matches.get(str(chat.id), [])) >= MAX_MATCHES_PER_GROUP:
        await update.message.reply_text(
            "Maximum matches reached! Wait for one to finish.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    match = create_match(chat.id, user.id, is_private=True, invited_user=username)
    if not match:
        await update.message.reply_text(
            "Could not create challenge! Try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    match["players"].append({
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    })
    
    challenge_text = (
        f"⚔️ *PRIVATE CHALLENGE!* ⚔️\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"**From:** {user.first_name}\n"
        f"**To:** @{username}\n"
        f"**Match ID:** `{match['match_id']}`\n\n"
        f"🔒 *Special Rules:*\n"
        f"• Only @{username} can join this match\n"
        f"• Others cannot join or spectate\n"
        f"• Auto-cancels in 2 minutes\n\n"
        f"@{username}, click below to accept challenge!"
    )
    
    keyboard = [
        [primary_btn("✅ ACCEPT CHALLENGE", f"accept_{match['match_id']}"),
         danger_btn("❌ DECLINE", f"decline_{match['match_id']}")]
    ]
    
    message = await update.message.reply_text(
        challenge_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["message_id"] = message.message_id

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    if not args:
        await matches_command(update, context)
        return
    
    match_id = args[0].upper()
    
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    match = get_match(match_id)
    if not match:
        await update.message.reply_text(
            f"Match `{match_id}` not found or expired.\n"
            f"Use `/matches` to see active matches.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if match["vs_ai"]:
        await update.message.reply_text(
            f"This is an AI match! You cannot join.\n"
            f"Use `/cricket ai` to play vs AI.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    user_data = {
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    }
    
    if join_match(match_id, user_data):
        match["state"] = "toss"
        
        await update.message.reply_text(
            f"✅ *Joined successfully!*\n\n"
            f"Match ID: `{match_id}`\n"
            f"Opponent: {match['players'][0]['name']}\n\n"
            f"Match starting...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        update_text = (
            f"🎮 *MATCH STARTED!*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{match_id}`\n\n"
            f"👥 *Players:*\n"
            f"1. {match['players'][0]['name']}\n"
            f"2. {match['players'][1]['name']}\n\n"
            f"🪙 *TOSS TIME!*\n"
            f"New joiner calls toss:"
        )
        
        keyboard = [
            [normal_btn("🌕 HEADS", f"heads_{match_id}"), 
             normal_btn("🌑 TAILS", f"tails_{match_id}")]
        ]
        
        try:
            await context.bot.edit_message_text(
                chat_id=match["chat_id"],
                message_id=match["message_id"],
                text=update_text,
                reply_markup={"inline_keyboard": keyboard},
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
    else:
        await update.message.reply_text(
            "❌ *Cannot join match!*\n\n"
            "Possible reasons:\n"
            "• Match is full\n"
            "• You're already in another match\n"
            "• Private match restrictions\n"
            "• Match is vs AI",
            parse_mode=ParseMode.MARKDOWN
        )

async def matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    match_ids = group_matches.get(str(chat.id), [])
    
    if not match_ids:
        await update.message.reply_text(
            "No active matches in this group.\n"
            "Start one with `/cricket`!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    matches_text = f"🏏 *ACTIVE MATCHES ({len(match_ids)}/{MAX_MATCHES_PER_GROUP})*\n━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, match_id in enumerate(match_ids[:5]):
        match = get_match(match_id)
        if match:
            status = "⏳ Waiting" if len(match["players"]) < 2 else "🎮 Playing"
            ai_tag = "🤖 AI" if match["vs_ai"] else ""
            lock_tag = "🔒" if match["is_private"] else ""
            
            players = []
            for p in match["players"]:
                if p["id"] == "ai_bot":
                    players.append("🤖 AI")
                else:
                    players.append(p["name"])
            
            players_text = " vs ".join(players) if players else "Waiting..."
            
            time_ago = int((datetime.utcnow() - match["last_activity"]).total_seconds())
            time_left = max(0, MATCH_TIMEOUT - time_ago)
            
            matches_text += (
                f"**{i+1}. {match_id}** {ai_tag}{lock_tag}\n"
                f"   👥 {players_text}\n"
                f"   📊 {status} | ⏳ {time_left}s\n\n"
            )
    
    matches_text += f"🎯 Join with: `/join MATCH_ID`"
    
    keyboard = []
    for match_id in match_ids[:3]:
        match = get_match(match_id)
        if match and len(match["players"]) < 2 and not match["vs_ai"]:
            keyboard.append([success_btn(f"🎮 JOIN {match_id}", f"join_{match_id}")])
    
    if keyboard:
        keyboard.append([
            normal_btn("🔄 REFRESH", "refresh_matches"),
            primary_btn("🎯 NEW MATCH", "play_cricket")
        ])
        reply_markup = {"inline_keyboard": keyboard}
    else:
        reply_markup = None
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            matches_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            matches_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    if args:
        match_id = args[0].upper()
        match = get_match(match_id)
        
        if not match:
            await update.message.reply_text(
                f"Match `{match_id}` not found.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        user_in_match = any(str(p["id"]) == str(user.id) for p in match.get("players", []))
        if not user_in_match and str(user.id) != match["created_by"]:
            await update.message.reply_text(
                "❌ You cannot cancel this match.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        remove_match(match_id)
        await update.message.reply_text(
            f"✅ Match `{match_id}` cancelled.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    match = get_user_match(user.id)
    
    if not match:
        for m in active_matches.values():
            if m["created_by"] == str(user.id) and len(m["players"]) < 2:
                match = m
                break
        
        if not match:
            await update.message.reply_text(
                "You are not in any match.\n"
                "To cancel specific match: `/cancel MATCH_ID`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    remove_match(match["match_id"])
    await update.message.reply_text(
        f"✅ Match `{match['match_id']}` cancelled.",
        parse_mode=ParseMode.MARKDOWN
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    if user.id == ADMIN_ID and update.effective_chat.type == ChatType.PRIVATE:
        total_users = len(set([p["id"] for match in active_matches.values() for p in match.get("players", []) if p["id"] != "ai_bot"]))
        total_groups = len(group_matches)
        total_matches = len(active_matches)
        ai_matches_count = len(ai_matches)
        
        admin_text = (
            f"🤖 *ADMIN DASHBOARD*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Active Users: `{total_users}`\n"
            f"👥 Total Groups: `{total_groups}`\n"
            f"🎮 Active Matches: `{total_matches}`\n"
            f"🤖 AI Matches: `{ai_matches_count}`\n\n"
            f"📊 *Group List:*\n"
        )
        
        for chat_id, matches in group_matches.items()[:5]:
            admin_text += f"• Group `{chat_id}` - `{len(matches)}` matches\n"
        
        if total_groups > 5:
            admin_text += f"• ... and `{total_groups - 5}` more groups\n"
        
        await update.message.reply_text(
            admin_text,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if args:
        target = args[0]
    
    stats_text = (
        f"📊 *PLAYER STATISTICS*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Player: {user.first_name}\n"
        f"📅 Joined: Today\n\n"
        f"🎮 Matches Played: 0\n"
        f"🏆 Wins: 0\n"
        f"💔 Losses: 0\n"
        f"🤖 AI Matches: 0\n\n"
        f"🔥 *Coming Soon:*\n"
        "• Detailed statistics\n"
        "• Leaderboard\n"
        "• Achievements\n"
        "• Tournament mode"
    )
    
    keyboard = [
        [normal_btn("🔄 REFRESH", f"refresh_stats_{user.id}"), 
         success_btn("📈 LEADERBOARD", "leaderboard")]
    ]
    
    await update.message.reply_text(
        stats_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )

# ================= CALLBACK HANDLER =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data
    
    await query.answer()
    
    if data == "play_cricket":
        await cricket_command(update, context)
    
    elif data == "play_vs_ai":
        await play_vs_ai_command(update, context)
    
    elif data == "play_friend":
        await play_friend_command(update, context)
    
    elif data == "play_ai_menu":
        await play_vs_ai_command(update, context)
    
    elif data == "play_friend_menu":
        await play_friend_command(update, context)
    
    elif data == "quick_play":
        await play_friend_command(update, context)
    
    elif data == "back_to_main":
        await start_command(update, context)
    
    elif data.startswith("ai_"):
        difficulty = data[3:]  # easy, medium, hard, expert
        await create_ai_match_helper(query, context, user, difficulty)
    
    elif data.startswith("join_"):
        await handle_join_callback(query, context, data[5:], user)
    
    elif data.startswith("accept_"):
        await handle_accept_callback(query, context, data[7:], user)
    
    elif data.startswith("cancel_"):
        await handle_cancel_callback(query, context, data[7:], user)
    
    elif data.startswith("switch_to_ai_"):
        match_id = data[13:]
        await switch_to_ai_callback(query, context, match_id, user)
    
    elif data == "my_stats":
        await stats_command(update, context)
    
    elif data == "refresh_matches":
        await matches_command(update, context)
    
    elif data == "new_match":
        await cricket_command(update, context)
    
    elif data == "leaderboard":
        await query.edit_message_text(
            "🏆 *LEADERBOARD COMING SOON!*\n\n"
            "Track rankings, scores, and achievements!\n\n"
            "Use `/stats` for personal statistics.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data.startswith("heads_") or data.startswith("tails_"):
        await handle_toss_callback(query, context, data, user)
    
    elif data.startswith("n1_") or data.startswith("n2_") or data.startswith("n3_") or \
         data.startswith("n4_") or data.startswith("n5_") or data.startswith("n6_"):
        await handle_ball_callback(query, context, data, user)
    
    elif data.startswith("surrender_"):
        await handle_surrender_callback(query, context, data[10:], user)
    
    else:
        await query.answer("Feature coming soon!", show_alert=True)

async def create_ai_match_helper(query, context, user, difficulty):
    """Helper to create AI match from callback"""
    match = await create_ai_match(user, query.message.chat, difficulty)
    if match:
        await query.edit_message_text(
            f"🤖 *AI MATCH STARTED!*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{match['match_id']}`\n"
            f"Player: {user.first_name}\n"
            f"AI Opponent: APEX AI ({difficulty.upper()})\n"
            f"Format: 1 Over | 2 Wickets\n\n"
            f"🎯 *TOSS TIME!*\n"
            f"Call heads or tails:",
            reply_markup={
                "inline_keyboard": [
                    [normal_btn("🌕 HEADS", f"heads_{match['match_id']}"), 
                     normal_btn("🌑 TAILS", f"tails_{match['match_id']}")]
                ]
            },
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.answer("Could not create AI match!", show_alert=True)

async def switch_to_ai_callback(query, context, match_id, user):
    """Switch waiting match to AI match"""
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    if match["created_by"] != str(user.id):
        await query.answer("Only match creator can switch to AI!", show_alert=True)
        return
    
    # Remove current match
    remove_match(match_id)
    
    # Create new AI match
    new_match = await create_ai_match(user, query.message.chat, "medium")
    if new_match:
        await query.edit_message_text(
            f"🔄 *SWITCHED TO AI MATCH!*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{new_match['match_id']}`\n"
            f"Player: {user.first_name}\n"
            f"AI Opponent: APEX AI (MEDIUM)\n"
            f"Format: 1 Over | 2 Wickets\n\n"
            f"🎯 *TOSS TIME!*\n"
            f"Call heads or tails:",
            reply_markup={
                "inline_keyboard": [
                    [normal_btn("🌕 HEADS", f"heads_{new_match['match_id']}"), 
                     normal_btn("🌑 TAILS", f"tails_{new_match['match_id']}")]
                ]
            },
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.answer("Could not switch to AI!", show_alert=True)

async def handle_join_callback(query, context, match_id, user):
    if str(user.id) in user_matches:
        await query.answer("You are already in a match!", show_alert=True)
        return
    
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    if match["vs_ai"]:
        await query.answer("This is an AI match! Use /cricket ai to play vs AI.", show_alert=True)
        return
    
    if match["is_private"] and match["invited_user"]:
        if user.username and user.username.lower() != match["invited_user"].lower():
            await query.answer(f"Private match! Only @{match['invited_user']} can join.", show_alert=True)
            return
    
    user_data = {
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    }
    
    if join_match(match_id, user_data):
        match["state"] = "toss"
        
        await query.answer("✅ Joined successfully!")
        
        update_text = (
            f"🎮 *MATCH STARTED!*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{match_id}`\n\n"
            f"👥 *Players:*\n"
            f"1. {match['players'][0]['name']}\n"
            f"2. {match['players'][1]['name']}\n\n"
            f"🪙 *TOSS TIME!*\n"
            f"New joiner calls toss:"
        )
        
        keyboard = [
            [normal_btn("🌕 HEADS", f"heads_{match_id}"), 
             normal_btn("🌑 TAILS", f"tails_{match_id}")]
        ]
        
        await query.edit_message_text(
            update_text,
            reply_markup={"inline_keyboard": keyboard},
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.answer("Cannot join match!", show_alert=True)

async def handle_accept_callback(query, context, match_id, user):
    match = get_match(match_id)
    if not match:
        await query.answer("Challenge not found!", show_alert=True)
        return
    
    if not match["is_private"]:
        await query.answer("This is not a private challenge!", show_alert=True)
        return
    
    if user.username and user.username.lower() != match["invited_user"].lower():
        await query.answer(f"This challenge is for @{match['invited_user']}, not you!", show_alert=True)
        return
    
    await handle_join_callback(query, context, match_id, user)

async def handle_cancel_callback(query, context, match_id, user):
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    user_in_match = any(str(p["id"]) == str(user.id) for p in match.get("players", []))
    if not user_in_match and str(user.id) != match["created_by"]:
        await query.answer("You cannot cancel this match!", show_alert=True)
        return
    
    remove_match(match_id)
    await query.answer("Match cancelled!")
    
    await query.edit_message_text(
        f"❌ *MATCH CANCELLED*\n\n"
        f"Match ID: `{match_id}`\n"
        f"Cancelled by: {user.first_name}",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_toss_callback(query, context, data, user):
    match_id = data[6:]
    match = get_match(match_id)
    
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    if not any(str(p["id"]) == str(user.id) for p in match.get("players", [])):
        await query.answer("You are not in this match!", show_alert=True)
        return
    
    toss_result = random.choice(["heads", "tails"])
    user_call = "heads" if data.startswith("heads_") else "tails"
    
    # Determine toss winner
    if user_call == toss_result:
        toss_winner = user
        result_text = f"🎉 {user.first_name} won the toss!"
        batting_first = user if not match["vs_ai"] else "human"
    else:
        if match["vs_ai"]:
            toss_winner = "ai"
            result_text = f"🤖 APEX AI won the toss!"
            batting_first = "ai"
        else:
            other_player = [p for p in match["players"] if str(p["id"]) != str(user.id)][0]
            toss_winner = other_player
            result_text = f"🎉 {other_player['name']} won the toss!"
            batting_first = other_player
    
    match["state"] = "inning1"
    match["toss_winner"] = toss_winner
    match["batting_first"] = batting_first
    
    # Set current batsman and bowler
    if match["vs_ai"]:
        if batting_first == "human":
            match["current_batsman"] = str(user.id)
            match["current_bowler"] = "ai_bot"
            batting_text = f"{user.first_name} chose to BAT first!"
        else:
            match["current_batsman"] = "ai_bot"
            match["current_bowler"] = str(user.id)
            batting_text = "🤖 APEX AI chose to BAT first!"
    else:
        # Human vs Human
        match["current_batsman"] = str(user.id) if batting_first == user else str(match["players"][1]["id"])
        match["current_bowler"] = str(match["players"][1]["id"]) if batting_first == user else str(user.id)
        batting_text = f"{toss_winner['name'] if isinstance(toss_winner, dict) else toss_winner} chose to BAT first!"
    
    update_text = (
        f"🪙 *TOSS RESULT*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match_id}`\n"
        f"Your call: {user_call.upper()}\n"
        f"Result: {toss_result.upper()}\n\n"
        f"{result_text}\n"
        f"{batting_text}\n\n"
        f"🏏 *FIRST INNINGS STARTING...*"
    )
    
    # Show game buttons
    keyboard = [
        [normal_btn("1️⃣", f"n1_{match_id}"), normal_btn("2️⃣", f"n2_{match_id}"), normal_btn("3️⃣", f"n3_{match_id}")],
        [normal_btn("4️⃣", f"n4_{match_id}"), normal_btn("5️⃣", f"n5_{match_id}"), normal_btn("6️⃣", f"n6_{match_id}")],
        [danger_btn("🏳️ SURRENDER", f"surrender_{match_id}")]
    ]
    
    await query.edit_message_text(
        update_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )
    
    await query.answer(f"Toss result: {toss_result.upper()}!")
    
    # If AI is batting first, make AI move
    if match["vs_ai"] and match["current_batsman"] == "ai_bot":
        await asyncio.sleep(1)
        await make_ai_move(query, context, match_id)

async def handle_ball_callback(query, context, data, user):
    """Handle ball selection (1-6)"""
    match_id = data[2:]  # Remove n1_, n2_, etc
    match = get_match(match_id)
    
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    if not any(str(p["id"]) == str(user.id) for p in match.get("players", [])):
        await query.answer("You are not in this match!", show_alert=True)
        return
    
    # Get ball number
    ball_number = int(data[1])
    
    # Process the ball
    if match["vs_ai"]:
        # Human vs AI match
        if match["current_batsman"] == str(user.id):
            # Human is batting, AI is bowling
            ball_result = await play_ball(match_id, ball_number)
            if ball_result:
                # Show result
                result_text = (
                    f"🎯 *BALL RESULT*\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"Your choice: {ball_number}\n"
                    f"AI's choice: {match['choices'].get('ai_bot', '?')}\n\n"
                    f"📊 {ball_result['result']}\n"
                    f"💬 {ball_result['commentary']}\n\n"
                    f"Score: {ball_result['score']}\n"
                    f"Overs: {ball_result['overs']}\n"
                    f"Wickets: {match['wickets']}/{match['max_wickets']}"
                )
                
                await query.edit_message_text(
                    result_text,
                    parse_mode=ParseMode.MARKDOWN
                )
                
                if ball_result["match_completed"]:
                    await end_match(query, context, match_id, ball_result["winner"])
                else:
                    # Continue game
                    await asyncio.sleep(2)
                    await continue_match(query, context, match_id)
        else:
            # AI is batting, human is bowling
            await query.answer("AI is batting! Wait for AI's move.", show_alert=True)
    else:
        # Human vs Human match
        await query.answer("Human vs Human gameplay coming soon!", show_alert=True)

async def make_ai_move(query, context, match_id):
    """Make AI move in the match"""
    match = get_match(match_id)
    if not match or not match["vs_ai"]:
        return
    
    if match["current_batsman"] == "ai_bot":
        # AI is batting
        ai_choice = match["ai_bot"].make_move(match)
        ball_result = await play_ball(match_id, ai_choice)
        
        if ball_result:
            result_text = (
                f"🤖 *AI'S MOVE*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"AI's choice: {ai_choice}\n"
                f"Your choice: {match['choices'].get(str(match['current_bowler']), '?')}\n\n"
                f"📊 {ball_result['result']}\n"
                f"💬 {match['ai_bot'].get_commentary(ai_choice, ball_result['is_wicket'])}\n\n"
                f"Score: {ball_result['score']}\n"
                f"Overs: {ball_result['overs']}\n"
                f"Wickets: {match['wickets']}/{match['max_wickets']}"
            )
            
            await query.edit_message_text(
                result_text,
                parse_mode=ParseMode.MARKDOWN
            )
            
            if ball_result["match_completed"]:
                await end_match(query, context, match_id, ball_result["winner"])
            else:
                # Switch to human's turn
                await asyncio.sleep(2)
                await continue_match(query, context, match_id)

async def continue_match(query, context, match_id):
    """Continue the match with next turn"""
    match = get_match(match_id)
    if not match:
        return
    
    # Show next turn
    if match["vs_ai"]:
        if match["current_batsman"] == "ai_bot":
            # AI just batted, now human's turn
            player = next((p for p in match["players"] if p["id"] != "ai_bot"), None)
            if player:
                update_text = (
                    f"🏏 *YOUR TURN TO BAT!*\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"Score: {match['score']}/{match['wickets']}\n"
                    f"Overs: {match['overs']}.{match['balls']}\n"
                    f"Balls left: {6 - match['balls']}\n\n"
                    f"Choose your shot (1-6):"
                )
        else:
            # Human just batted, now AI's turn
            update_text = (
                f"🎯 *AI'S TURN TO BOWL!*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Score: {match['score']}/{match['wickets']}\n"
                f"Overs: {match['overs']}.{match['balls']}\n"
                f"Balls left: {6 - match['balls']}\n\n"
                f"AI is making its move..."
            )
    else:
        update_text = (
            f"🏏 *CONTINUE MATCH*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Score: {match['score']}/{match['wickets']}\n"
            f"Overs: {match['overs']}.{match['balls']}\n"
            f"Choose your move:"
        )
    
    keyboard = [
        [normal_btn("1️⃣", f"n1_{match_id}"), normal_btn("2️⃣", f"n2_{match_id}"), normal_btn("3️⃣", f"n3_{match_id}")],
        [normal_btn("4️⃣", f"n4_{match_id}"), normal_btn("5️⃣", f"n5_{match_id}"), normal_btn("6️⃣", f"n6_{match_id}")],
        [danger_btn("🏳️ SURRENDER", f"surrender_{match_id}")]
    ]
    
    await query.edit_message_text(
        update_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )
    
    # If it's AI's turn, make AI move
    if match["vs_ai"] and match["current_batsman"] == "ai_bot":
        await asyncio.sleep(1)
        await make_ai_move(query, context, match_id)

async def handle_surrender_callback(query, context, match_id, user):
    """Handle surrender"""
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    if not any(str(p["id"]) == str(user.id) for p in match.get("players", [])):
        await query.answer("You are not in this match!", show_alert=True)
        return
    
    # Determine winner
    if match["vs_ai"]:
        winner = "🤖 APEX AI" if match["current_batsman"] == str(user.id) else user.first_name
    else:
        other_player = [p for p in match["players"] if str(p["id"]) != str(user.id)][0]
        winner = other_player["name"]
    
    await end_match(query, context, match_id, winner, surrendered=True)

async def end_match(query, context, match_id, winner, surrendered=False):
    """End the match and show results"""
    match = get_match(match_id)
    if not match:
        return
    
    # Get player names
    human_player = next((p for p in match["players"] if p["id"] != "ai_bot"), None)
    ai_player = next((p for p in match["players"] if p["id"] == "ai_bot"), None)
    
    if surrendered:
        result_text = "🏳️ SURRENDERED!"
    else:
        result_text = "🏆 MATCH COMPLETED!"
    
    final_text = (
        f"{result_text}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match_id}`\n\n"
    )
    
    if match["vs_ai"]:
        final_text += (
            f"👥 *Players:*\n"
            f"• {human_player['name'] if human_player else 'Player'}\n"
            f"• 🤖 APEX AI ({match['ai_difficulty'].upper()})\n\n"
        )
        
        if isinstance(winner, str) and "AI" in winner:
            final_text += f"🏆 *Winner:* 🤖 APEX AI\n"
            final_text += f"💔 *Loser:* {human_player['name'] if human_player else 'Player'}\n"
        else:
            final_text += f"🏆 *Winner:* {human_player['name'] if human_player else 'Player'}\n"
            final_text += f"💔 *Loser:* 🤖 APEX AI\n"
    else:
        final_text += (
            f"👥 *Players:*\n"
            f"• {match['players'][0]['name']}\n"
            f"• {match['players'][1]['name']}\n\n"
            f"🏆 *Winner:* {winner}\n"
        )
    
    final_text += (
        f"\n📊 *Final Score:* {match['score']}/{match['wickets']}\n"
        f"⏰ *Duration:* {int((datetime.utcnow() - match['created_at']).total_seconds())}s\n\n"
        f"🎮 Play again with `/cricket`!"
    )
    
    keyboard = [
        [primary_btn("🎮 PLAY AGAIN", "play_cricket"), success_btn("📊 STATS", "my_stats")]
    ]
    
    await query.edit_message_text(
        final_text,
        reply_markup={"inline_keyboard": keyboard},
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Remove match
    remove_match(match_id)

# ================= CLEANUP TASK =================
async def cleanup_task():
    while True:
        try:
            expired = cleanup_expired_matches()
            if expired > 0:
                logger.info(f"🧹 Cleaned up {expired} expired matches")
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            await asyncio.sleep(60)

# ================= FLASK SERVER =================
def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ================= MAIN BOT =================
async def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cricket", cricket_command))
    application.add_handler(CommandHandler("play", cricket_command))  # Alias for /play
    application.add_handler(CommandHandler("challenge", challenge_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("matches", matches_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Add callback handler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Start cleanup task
    asyncio.create_task(cleanup_task())
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    logger.info("✅ APEX CRICKET BOT with AI is running!")
    
    while True:
        await asyncio.sleep(3600)

# ================= START BOTH SERVERS =================
if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    asyncio.run(main())
