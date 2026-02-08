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

MATCH_TIMEOUT = 120
MAX_MATCHES_PER_GROUP = 10

# ================= AI BOT SYSTEM =================
class AICricketBot:
    """AI opponent with different difficulty levels"""
    
    def __init__(self, difficulty="medium"):
        self.difficulty = difficulty
        self.name = "🤖 APEX AI"
        self.id = "ai_bot"
    
    def make_move(self):
        """AI makes a cricket move (1-6) based on difficulty"""
        if self.difficulty == "easy":
            return random.randint(1, 3) if random.random() < 0.7 else random.randint(1, 6)
        elif self.difficulty == "medium":
            return random.randint(1, 6)
        elif self.difficulty == "hard":
            return random.randint(4, 6) if random.random() < 0.6 else random.randint(1, 3)
        else:
            return random.randint(1, 6)

# ================= MATCH MANAGEMENT =================
def create_match(chat_id, created_by, vs_ai=False, ai_difficulty="medium", is_private=False, invited_user=None):
    if len(group_matches.get(str(chat_id), [])) >= MAX_MATCHES_PER_GROUP:
        return None
    
    match_id = str(uuid.uuid4())[:6].upper()
    
    match = {
        "match_id": match_id,
        "chat_id": str(chat_id),
        "created_by": str(created_by),
        "vs_ai": vs_ai,
        "ai_difficulty": ai_difficulty,
        "ai_bot": AICricketBot(ai_difficulty) if vs_ai else None,
        "is_private": is_private,
        "invited_user": invited_user,
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
        "toss_winner": None,
        "batting_first": None,
        "inning": 1,
        "choices": {}
    }
    
    active_matches[match_id] = match
    user_matches[str(created_by)] = match_id
    
    if vs_ai:
        # AI joins automatically
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
    
    logger.info(f"🎮 Match created: {match_id}")
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
        if user_data.get("username", "").lower() != match["invited_user"].lower():
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

# ================= GAME FUNCTIONS =================
def process_ball(match, batsman_choice, bowler_choice):
    """Process a ball in the match"""
    match["last_activity"] = datetime.utcnow()
    
    is_wicket = (batsman_choice == bowler_choice)
    
    if is_wicket:
        match["wickets"] += 1
        result = f"☝️ OUT! {batsman_choice} = {bowler_choice}"
        commentary = "🎯 WICKET!"
    else:
        match["score"] += batsman_choice
        result = f"✨ {batsman_choice} runs! {batsman_choice} ≠ {bowler_choice}"
        commentary = f"🏏 {batsman_choice} runs scored!"
    
    match["balls"] += 1
    if match["balls"] == 6:
        match["overs"] += 1
        match["balls"] = 0
    
    # Check if innings complete
    if match["wickets"] >= 2 or match["overs"] >= 1:
        if match["inning"] == 1:
            match["target"] = match["score"] + 1
            match["inning"] = 2
            match["score"] = 0
            match["wickets"] = 0
            match["overs"] = 0
            match["balls"] = 0
            return {
                "result": result,
                "commentary": commentary,
                "is_wicket": is_wicket,
                "runs": 0 if is_wicket else batsman_choice,
                "innings_complete": True,
                "target": match["target"]
            }
        else:
            return {
                "result": result,
                "commentary": commentary,
                "is_wicket": is_wicket,
                "runs": 0 if is_wicket else batsman_choice,
                "match_complete": True,
                "winner": "batsman" if match["score"] >= match["target"] else "bowler"
            }
    
    if match["inning"] == 2 and match["target"] and match["score"] >= match["target"]:
        return {
            "result": result,
            "commentary": commentary,
            "is_wicket": is_wicket,
            "runs": 0 if is_wicket else batsman_choice,
            "match_complete": True,
            "winner": "batsman"
        }
    
    return {
        "result": result,
        "commentary": commentary,
        "is_wicket": is_wicket,
        "runs": 0 if is_wicket else batsman_choice,
        "innings_complete": False,
        "match_complete": False
    }

# ================= COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    welcome_text = (
        "🏏 APEX CRICKET BOT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome to the ultimate hand cricket experience!\n\n"
        "🎮 Play vs 🤖 APEX AI or challenge friends!\n\n"
        "🚀 Commands:\n"
        "/cricket - Start a match\n"
        "/challenge @username - Challenge friend\n"
        "/join MATCHID - Join match\n"
        "/matches - View active matches\n"
        "/cancel - Cancel your match\n"
        "/stats - View statistics\n\n"
        f"⚡ Up to {MAX_MATCHES_PER_GROUP} matches simultaneously!"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🎮 PLAY CRICKET", callback_data="cricket_menu"),
            InlineKeyboardButton("📊 MY STATS", callback_data="my_stats")
        ]
    ]
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def cricket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    match_type_text = (
        "🎮 CHOOSE MATCH TYPE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "How do you want to play?\n\n"
        "1. 🤖 VS AI - Play against APEX AI Bot\n"
        "2. 👥 VS FRIEND - Public match\n"
        "3. 🔒 PRIVATE - Challenge specific friend\n\n"
        "Click buttons below:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🤖 VS AI", callback_data="play_ai"),
            InlineKeyboardButton("👥 VS FRIEND", callback_data="play_friend")
        ],
        [
            InlineKeyboardButton("🔒 PRIVATE CHALLENGE", callback_data="challenge_menu")
        ]
    ]
    
    await update.message.reply_text(
        match_type_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def play_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Show AI difficulty selection
    ai_text = (
        "🤖 CHOOSE AI DIFFICULTY\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Select AI opponent difficulty:\n\n"
        "🟢 EASY - Perfect for beginners\n"
        "🟡 MEDIUM - Balanced challenge\n"
        "🔴 HARD - For experienced players\n\n"
        "Format: 1 Over | 2 Wickets"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🟢 EASY", callback_data="ai_easy"),
            InlineKeyboardButton("🟡 MEDIUM", callback_data="ai_medium"),
            InlineKeyboardButton("🔴 HARD", callback_data="ai_hard")
        ],
        [
            InlineKeyboardButton("❌ BACK", callback_data="cricket_menu")
        ]
    ]
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            ai_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            ai_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def create_ai_match(user, chat, difficulty):
    """Create and start AI match"""
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
        "username": user.username
    })
    
    match["state"] = "toss"
    
    match_text = (
        f"🤖 AI MATCH STARTED!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match['match_id']}`\n"
        f"Player: {user.first_name}\n"
        f"AI: APEX AI ({difficulty.upper()})\n"
        f"Format: 1 Over | 2 Wickets\n\n"
        f"🎯 TOSS TIME!\n"
        f"Call heads or tails:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match['match_id']}"),
            InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match['match_id']}")
        ]
    ]
    
    message = await context.bot.send_message(
        chat_id=chat.id,
        text=match_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["message_id"] = message.message_id
    return match

async def play_friend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    match = create_match(chat.id, user.id, vs_ai=False)
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
        f"🎮 MATCH CREATED!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match['match_id']}`\n"
        f"Created by: {user.first_name}\n"
        f"Type: Public Match\n"
        f"⏳ Timeout: 2 minutes\n\n"
        f"🔹 Players (1/2):\n"
        f"1. {user.first_name}\n"
        f"2. Waiting for opponent...\n\n"
        f"🎯 To join: /join {match['match_id']}\n\n"
        f"⏰ Auto-cancels in 2 minutes"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ JOIN MATCH", callback_data=f"join_{match['match_id']}"),
            InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_{match['match_id']}")
        ]
    ]
    
    message = await update.message.reply_text(
        match_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["message_id"] = message.message_id

async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "Usage: /challenge @username\n\n"
            "Example: /challenge @john\n\n"
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
    
    match = create_match(chat.id, user.id, vs_ai=False, is_private=True, invited_user=username)
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
        f"⚔️ PRIVATE CHALLENGE!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"From: {user.first_name}\n"
        f"To: @{username}\n"
        f"Match ID: `{match['match_id']}`\n\n"
        f"🔒 Only @{username} can join!\n\n"
        f"@{username}, click below to accept:"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ ACCEPT CHALLENGE", callback_data=f"accept_{match['match_id']}"),
            InlineKeyboardButton("❌ DECLINE", callback_data=f"decline_{match['match_id']}")
        ]
    ]
    
    message = await update.message.reply_text(
        challenge_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
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
            f"Match `{match_id}` not found or expired.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if match["vs_ai"]:
        await update.message.reply_text(
            f"This is an AI match! Use `/cricket` to play vs AI.",
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
            f"✅ Joined successfully!\n\n"
            f"Match ID: `{match_id}`\n"
            f"Opponent: {match['players'][0]['name']}\n\n"
            f"Match starting...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        update_text = (
            f"🎮 MATCH STARTED!\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{match_id}`\n\n"
            f"👥 Players:\n"
            f"1. {match['players'][0]['name']}\n"
            f"2. {match['players'][1]['name']}\n\n"
            f"🪙 TOSS TIME!\n"
            f"New joiner calls toss:"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match_id}"),
                InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match_id}")
            ]
        ]
        
        try:
            await context.bot.edit_message_text(
                chat_id=match["chat_id"],
                message_id=match["message_id"],
                text=update_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
    else:
        await update.message.reply_text(
            "❌ Cannot join match!\n\n"
            "Possible reasons:\n"
            "• Match is full\n"
            "• You're already in another match\n"
            "• Private match restrictions",
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
    
    matches_text = f"🏏 ACTIVE MATCHES ({len(match_ids)}/{MAX_MATCHES_PER_GROUP})\n━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, match_id in enumerate(match_ids[:5]):
        match = get_match(match_id)
        if match:
            status = "⏳ Waiting" if len(match["players"]) < 2 else "🎮 Playing"
            ai_tag = "🤖" if match["vs_ai"] else ""
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
                f"{i+1}. {match_id} {ai_tag}{lock_tag}\n"
                f"   👥 {players_text}\n"
                f"   📊 {status} | ⏳ {time_left}s\n\n"
            )
    
    matches_text += f"🎯 Join with: `/join MATCH_ID`"
    
    keyboard = []
    for match_id in match_ids[:3]:
        match = get_match(match_id)
        if match and len(match["players"]) < 2 and not match["vs_ai"]:
            keyboard.append([InlineKeyboardButton(f"🎮 JOIN {match_id}", callback_data=f"join_{match_id}")])
    
    if keyboard:
        keyboard.append([
            InlineKeyboardButton("🔄 REFRESH", callback_data="refresh_matches"),
            InlineKeyboardButton("🎯 NEW MATCH", callback_data="cricket_menu")
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)
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
        
        admin_text = (
            f"🤖 ADMIN DASHBOARD\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Active Users: `{total_users}`\n"
            f"👥 Total Groups: `{total_groups}`\n"
            f"🎮 Active Matches: `{total_matches}`\n\n"
            f"📊 Group List:\n"
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
        f"📊 PLAYER STATISTICS\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Player: {user.first_name}\n"
        f"📅 Joined: Today\n\n"
        f"🎮 Matches Played: 0\n"
        f"🏆 Wins: 0\n"
        f"💔 Losses: 0\n\n"
        f"🔥 Coming Soon:\n"
        "• Detailed statistics\n"
        "• Leaderboard\n"
        "• Achievements"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 REFRESH", callback_data=f"refresh_stats_{user.id}"),
            InlineKeyboardButton("📈 LEADERBOARD", callback_data="leaderboard")
        ]
    ]
    
    await update.message.reply_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

# ================= CALLBACK HANDLER =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data
    
    await query.answer()
    
    # Menu navigation
    if data == "cricket_menu":
        await cricket_command(update, context)
    
    elif data == "play_ai":
        await play_ai_command(update, context)
    
    elif data == "play_friend":
        await play_friend_command(update, context)
    
    elif data == "challenge_menu":
        await query.edit_message_text(
            "🔒 PRIVATE CHALLENGE\n\n"
            "To challenge a friend:\n"
            "Use command: /challenge @username\n\n"
            "Example: /challenge @john\n\n"
            "Only that user can join!",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "my_stats":
        await stats_command(update, context)
    
    elif data == "refresh_matches":
        await matches_command(update, context)
    
    elif data == "leaderboard":
        await query.edit_message_text(
            "🏆 LEADERBOARD COMING SOON!\n\n"
            "Track rankings and scores!\n\n"
            "Use `/stats` for personal statistics.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    # AI difficulty selection
    elif data.startswith("ai_"):
        difficulty = data[3:]  # easy, medium, hard
        await handle_ai_difficulty(query, context, user, difficulty)
    
    # Match actions
    elif data.startswith("join_"):
        await handle_join_callback(query, context, data[5:], user)
    
    elif data.startswith("accept_"):
        await handle_accept_callback(query, context, data[7:], user)
    
    elif data.startswith("cancel_"):
        await handle_cancel_callback(query, context, data[7:], user)
    
    # Game actions
    elif data.startswith("heads_") or data.startswith("tails_"):
        await handle_toss_callback(query, context, data, user)
    
    elif data.startswith("n1_") or data.startswith("n2_") or data.startswith("n3_") or \
         data.startswith("n4_") or data.startswith("n5_") or data.startswith("n6_"):
        await handle_ball_callback(query, context, data, user)
    
    elif data.startswith("surrender_"):
        await handle_surrender_callback(query, context, data[10:], user)
    
    else:
        await query.answer("Feature coming soon!", show_alert=True)

async def handle_ai_difficulty(query, context, user, difficulty):
    """Handle AI difficulty selection"""
    match = await create_ai_match(user, query.message.chat, difficulty)
    if match:
        await query.edit_message_text(
            f"🤖 AI MATCH STARTED!\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{match['match_id']}`\n"
            f"Player: {user.first_name}\n"
            f"AI: APEX AI ({difficulty.upper()})\n"
            f"Format: 1 Over | 2 Wickets\n\n"
            f"🎯 TOSS TIME!\n"
            f"Call heads or tails:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match['match_id']}"),
                    InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match['match_id']}")
                ]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.answer("Could not create AI match!", show_alert=True)

async def handle_join_callback(query, context, match_id, user):
    if str(user.id) in user_matches:
        await query.answer("You are already in a match!", show_alert=True)
        return
    
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    if match["vs_ai"]:
        await query.answer("This is an AI match! Use /cricket to play vs AI.", show_alert=True)
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
            f"🎮 MATCH STARTED!\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: `{match_id}`\n\n"
            f"👥 Players:\n"
            f"1. {match['players'][0]['name']}\n"
            f"2. {match['players'][1]['name']}\n\n"
            f"🪙 TOSS TIME!\n"
            f"New joiner calls toss:"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match_id}"),
                InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match_id}")
            ]
        ]
        
        await query.edit_message_text(
            update_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
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
        f"❌ MATCH CANCELLED\n\n"
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
    is_user_winner = (user_call == toss_result)
    
    if match["vs_ai"]:
        if is_user_winner:
            toss_text = f"🎉 {user.first_name} won the toss!\nYou choose to BAT first!"
            match["batting_first"] = "human"
        else:
            toss_text = f"🤖 APEX AI won the toss!\nAI chooses to BAT first!"
            match["batting_first"] = "ai"
    else:
        other_player = [p for p in match["players"] if str(p["id"]) != str(user.id)][0]
        if is_user_winner:
            toss_text = f"🎉 {user.first_name} won the toss!\nYou choose to BAT first!"
            match["batting_first"] = str(user.id)
        else:
            toss_text = f"🎉 {other_player['name']} won the toss!\nThey choose to BAT first!"
            match["batting_first"] = str(other_player["id"])
    
    match["state"] = "inning1"
    match["toss_winner"] = str(user.id) if is_user_winner else "ai_bot" if match["vs_ai"] else str(other_player["id"])
    
    update_text = (
        f"🪙 TOSS RESULT\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: `{match_id}`\n"
        f"Your call: {user_call.upper()}\n"
        f"Result: {toss_result.upper()}\n\n"
        f"{toss_text}\n\n"
        f"🏏 FIRST INNINGS STARTING..."
    )
    
    # Show game buttons
    keyboard = [
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
        [
            InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{match_id}")
        ]
    ]
    
    await query.edit_message_text(
        update_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    
    await query.answer(f"Toss result: {toss_result.upper()}!")
    
    # If AI is batting first in AI match, make AI move
    if match["vs_ai"] and match["batting_first"] == "ai":
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
    
    # Determine if user is batting or bowling
    is_batting = False
    if match["vs_ai"]:
        if match["batting_first"] == "human":
            is_batting = True
        else:
            is_batting = False
    else:
        # Human vs human - need to check current state
        is_batting = True  # Simplified for now
    
    if match["vs_ai"]:
        # Human vs AI match
        if is_batting:
            # Human is batting, AI is bowling
            ai_choice = match["ai_bot"].make_move()
            ball_result = process_ball(match, ball_number, ai_choice)
            
            result_text = (
                f"🎯 BALL RESULT\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Your choice: {ball_number}\n"
                f"AI's choice: {ai_choice}\n\n"
                f"📊 {ball_result['result']}\n"
                f"💬 {ball_result['commentary']}\n\n"
                f"Score: {match['score']}/{match['wickets']}\n"
                f"Overs: {match['overs']}.{match['balls']}"
            )
            
            await query.edit_message_text(
                result_text,
                parse_mode=ParseMode.MARKDOWN
            )
            
            if ball_result.get("match_complete"):
                await end_match(query, context, match_id, ball_result["winner"])
            elif ball_result.get("innings_complete"):
                await switch_innings(query, context, match_id)
            else:
                await asyncio.sleep(2)
                await continue_game(query, context, match_id)
        else:
            # AI is batting, human is bowling
            ai_choice = match["ai_bot"].make_move()
            ball_result = process_ball(match, ai_choice, ball_number)
            
            result_text = (
                f"🤖 AI'S MOVE\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"AI's choice: {ai_choice}\n"
                f"Your choice: {ball_number}\n\n"
                f"📊 {ball_result['result']}\n"
                f"💬 {ball_result['commentary']}\n\n"
                f"Score: {match['score']}/{match['wickets']}\n"
                f"Overs: {match['overs']}.{match['balls']}"
            )
            
            await query.edit_message_text(
                result_text,
                parse_mode=ParseMode.MARKDOWN
            )
            
            if ball_result.get("match_complete"):
                await end_match(query, context, match_id, ball_result["winner"])
            elif ball_result.get("innings_complete"):
                await switch_innings(query, context, match_id)
            else:
                await asyncio.sleep(2)
                await continue_game(query, context, match_id)
    else:
        # Human vs Human (simplified)
        await query.answer("Human vs Human gameplay!", show_alert=True)

async def make_ai_move(query, context, match_id):
    """Make AI move in AI match"""
    match = get_match(match_id)
    if not match or not match["vs_ai"]:
        return
    
    if match["batting_first"] == "ai":
        # AI is batting
        ai_choice = match["ai_bot"].make_move()
        # In actual game, would wait for human bowler choice
        pass

async def continue_game(query, context, match_id):
    """Continue the game with next turn"""
    match = get_match(match_id)
    if not match:
        return
    
    if match["vs_ai"]:
        if match["batting_first"] == "human":
            update_text = (
                f"🏏 YOUR TURN TO BAT!\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Score: {match['score']}/{match['wickets']}\n"
                f"Overs: {match['overs']}.{match['balls']}\n"
                f"Balls left: {6 - match['balls']}\n\n"
                f"Choose your shot (1-6):"
            )
        else:
            update_text = (
                f"🎯 YOUR TURN TO BOWL!\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"AI Score: {match['score']}/{match['wickets']}\n"
                f"Overs: {match['overs']}.{match['balls']}\n"
                f"Balls left: {6 - match['balls']}\n\n"
                f"Choose your delivery (1-6):"
            )
    else:
        update_text = (
            f"🏏 CONTINUE MATCH\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Score: {match['score']}/{match['wickets']}\n"
            f"Overs: {match['overs']}.{match['balls']}\n"
            f"Choose your move:"
        )
    
    keyboard = [
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
        [
            InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{match_id}")
        ]
    ]
    
    await query.edit_message_text(
        update_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def switch_innings(query, context, match_id):
    """Switch to second innings"""
    match = get_match(match_id)
    if not match:
        return
    
    if match["inning"] == 2:
        # Already in second innings
        await continue_game(query, context, match_id)
        return
    
    update_text = (
        f"🔄 INNINGS SWITCH!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"First innings complete!\n\n"
        f"Target: {match['target']} runs\n\n"
        f"🏏 SECOND INNINGS STARTING..."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("1️⃣", callback_data=f"n1_{match_id}"),
            InlineKeyboardButton("2️⃣", callback_data=f"n2_{match_id}"),
            InlineKeyboardButton("3️⃣", callback_data=f"n3_{match_id}")
        ],
        [
            InlineKeyboardButton("4️⃣", callback_data=f"n4_{match_id}"),
            InlineKeyboardButton("5️⃣", callback_data=f"n5_{match_id}"),
            InlineKeyboardButton("6️⃣", callback_data=f"n6_{match_id}")
        ]
    ]
    
    await query.edit_message_text(
        update_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["inning"] = 2

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
        winner = "🤖 APEX AI"
        loser = user.first_name
    else:
        other_player = [p for p in match["players"] if str(p["id"]) != str(user.id)][0]
        winner = other_player["name"]
        loser = user.first_name
    
    await end_match(query, context, match_id, winner, surrendered=True)

async def end_match(query, context, match_id, winner, surrendered=False):
    """End the match and show results"""
    match = get_match(match_id)
    if not match:
        return
    
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
        human_player = next((p for p in match["players"] if p["id"] != "ai_bot"), None)
        final_text += (
            f"👥 Players:\n"
            f"• {human_player['name'] if human_player else 'Player'}\n"
            f"• 🤖 APEX AI ({match['ai_difficulty'].upper()})\n\n"
        )
        
        if "AI" in winner:
            final_text += f"🏆 Winner: 🤖 APEX AI\n"
            final_text += f"💔 Loser: {human_player['name'] if human_player else 'Player'}\n"
        else:
            final_text += f"🏆 Winner: {human_player['name'] if human_player else 'Player'}\n"
            final_text += f"💔 Loser: 🤖 APEX AI\n"
    else:
        final_text += (
            f"👥 Players:\n"
            f"• {match['players'][0]['name']}\n"
            f"• {match['players'][1]['name']}\n\n"
            f"🏆 Winner: {winner}\n"
        )
    
    final_text += (
        f"\n📊 Final Score: {match['score']}/{match['wickets']}\n"
        f"⏰ Duration: {int((datetime.utcnow() - match['created_at']).total_seconds())}s\n\n"
        f"🎮 Play again with `/cricket`!"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🎮 PLAY AGAIN", callback_data="cricket_menu"),
            InlineKeyboardButton("📊 STATS", callback_data="my_stats")
        ]
    ]
    
    await query.edit_message_text(
        final_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
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
    application.add_handler(CommandHandler("play", cricket_command))
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
    
    logger.info("✅ APEX CRICKET BOT is running!")
    
    while True:
        await asyncio.sleep(3600)

# ================= START BOTH SERVERS =================
if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    asyncio.run(main())
