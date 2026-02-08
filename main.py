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

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
PORT = int(os.environ.get('PORT', 5000))
ADMIN_ID = 5298223577  # Your admin ID

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# MongoDB
client = None
db = None

# In-memory storage
active_matches = {}
user_matches = {}
group_matches = defaultdict(list)
private_invites = {}
match_messages = {}

# Constants
MATCH_TIMEOUT = 120  # 2 minutes
MAX_MATCHES_PER_GROUP = 10

# ================= DATABASE FUNCTIONS =================
async def init_db():
    global client, db
    try:
        if MONGODB_URI:
            client = pymongo.MongoClient(MONGODB_URI)
            db = client.cricket_bot
            logger.info("✅ MongoDB connected")
    except Exception as e:
        logger.error(f"❌ MongoDB error: {e}")

async def save_match_stats(match_data):
    try:
        if db:
            db.matches.insert_one(match_data)
    except Exception as e:
        logger.error(f"Error saving match: {e}")

async def get_player_stats(user_id):
    try:
        if db:
            stats = db.stats.find_one({"user_id": str(user_id)})
            return stats
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
    return None

# ================= MATCH MANAGEMENT =================
def create_match(chat_id, created_by, is_private=False, invited_user=None):
    """Create a new match"""
    # Check group limit
    if len(group_matches.get(str(chat_id), [])) >= MAX_MATCHES_PER_GROUP:
        return None
    
    match_id = str(uuid.uuid4())[:6].upper()
    
    match = {
        "match_id": match_id,
        "chat_id": str(chat_id),
        "created_by": str(created_by),
        "is_private": is_private,
        "invited_user": invited_user,
        "players": [],
        "state": "waiting",  # waiting, toss, inning1, inning2, completed
        "score": 0,
        "wickets": 0,
        "overs": 0,
        "balls": 0,
        "target": None,
        "created_at": datetime.utcnow(),
        "last_activity": datetime.utcnow(),
        "message_id": None
    }
    
    active_matches[match_id] = match
    user_matches[str(created_by)] = match_id
    
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
    """Join an existing match"""
    match = get_match(match_id)
    if not match:
        return False
    
    # Check if full
    if len(match["players"]) >= 2:
        return False
    
    # Check if private
    if match["is_private"] and match["invited_user"]:
        if user_data["username"] and user_data["username"].lower() != match["invited_user"].lower():
            return False
    
    # Check if already in a match
    if str(user_data["id"]) in user_matches:
        return False
    
    # Add player
    match["players"].append(user_data)
    match["last_activity"] = datetime.utcnow()
    user_matches[str(user_data["id"])] = match_id
    
    logger.info(f"👤 Player {user_data['id']} joined match {match_id}")
    return True

def remove_match(match_id):
    """Remove a match"""
    match = get_match(match_id)
    if not match:
        return
    
    # Remove from user_matches
    for player in match.get("players", []):
        user_id = str(player.get("id"))
        if user_id in user_matches:
            del user_matches[user_id]
    
    # Remove from group_matches
    chat_id = match.get("chat_id")
    if chat_id in group_matches and match_id in group_matches[chat_id]:
        group_matches[chat_id].remove(match_id)
    
    # Remove from private_invites
    for user_id, mid in list(private_invites.items()):
        if mid == match_id:
            del private_invites[user_id]
    
    # Remove from active_matches
    if match_id in active_matches:
        del active_matches[match_id]
    
    logger.info(f"🗑️ Match removed: {match_id}")

def cleanup_expired_matches():
    """Clean up expired matches"""
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

# ================= COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    welcome_text = (
        "🏏 APEX CRICKET BOT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome to the ultimate hand cricket experience!\n\n"
        "Features:\n"
        "• Multiple matches in same group\n"
        "• Private challenges\n"
        "• Auto-cleanup after 2 mins\n\n"
        "Commands:\n"
        "/play - Start a match\n"
        "/challenge @username - Challenge friend\n"
        "/join MATCHID - Join match\n"
        "/matches - View active matches\n"
        "/cancel - Cancel your match\n"
        "/stats - View statistics\n\n"
        f"Up to {MAX_MATCHES_PER_GROUP} matches simultaneously!"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🎮 PLAY NOW", callback_data="play_now"),
            InlineKeyboardButton("📊 MY STATS", callback_data="my_stats")
        ],
        [
            InlineKeyboardButton("👥 VS FRIEND", callback_data="play_friend"),
            InlineKeyboardButton("🏆 LEADERBOARD", callback_data="leaderboard")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /play command"""
    user = update.effective_user
    chat = update.effective_chat
    
    # Check if already in match
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check group limit
    if len(group_matches.get(str(chat.id), [])) >= MAX_MATCHES_PER_GROUP:
        await update.message.reply_text(
            f"Maximum matches reached in this group! Only {MAX_MATCHES_PER_GROUP} matches can run simultaneously.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Create match
    match = create_match(chat.id, user.id)
    if not match:
        await update.message.reply_text(
            "Could not create match! Try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Add creator
    match["players"].append({
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    })
    
    match_text = (
        f"🎮 Match Created!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: {match['match_id']}\n"
        f"Created by: {user.first_name}\n"
        f"Status: Waiting for opponent...\n\n"
        f"To join: /join {match['match_id']}\n"
        f"Auto-cancels in 2 minutes if no one joins."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🎮 JOIN MATCH", callback_data=f"join_{match['match_id']}"),
            InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_{match['match_id']}")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        match_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Store message ID
    match["message_id"] = message.message_id
    match_messages[match['match_id']] = message.message_id

async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /challenge command"""
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    if not args:
        await update.message.reply_text(
            "Usage: /challenge @username\n\nOnly that user can join your match.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    username = args[0]
    if username.startswith('@'):
        username = username[1:]
    
    # Check if already in match
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check group limit
    if len(group_matches.get(str(chat.id), [])) >= MAX_MATCHES_PER_GROUP:
        await update.message.reply_text(
            "Maximum matches reached! Wait for one to finish.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Create private match
    match = create_match(chat.id, user.id, is_private=True, invited_user=username)
    if not match:
        await update.message.reply_text(
            "Could not create challenge! Try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Add creator
    match["players"].append({
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    })
    
    challenge_text = (
        f"⚔️ Private Challenge!\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"From: {user.first_name}\n"
        f"To: @{username}\n"
        f"Match ID: {match['match_id']}\n\n"
        f"Only @{username} can join this match!"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ ACCEPT CHALLENGE", callback_data=f"accept_{match['match_id']}")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        challenge_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    match["message_id"] = message.message_id
    match_messages[match['match_id']] = message.message_id

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /join command"""
    user = update.effective_user
    args = context.args
    
    if not args:
        await matches_command(update, context)
        return
    
    match_id = args[0].upper()
    
    # Check if already in match
    if str(user.id) in user_matches:
        await update.message.reply_text(
            "You are already in a match! Use /cancel first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Get match
    match = get_match(match_id)
    if not match:
        await update.message.reply_text(
            f"Match {match_id} not found or expired.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Create user data
    user_data = {
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    }
    
    # Join match
    if join_match(match_id, user_data):
        match["state"] = "toss"
        
        await update.message.reply_text(
            f"✅ Joined match {match_id}!\n\n"
            f"Opponent: {match['players'][0]['name']}\n"
            f"Match starting...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Update match message
        update_text = (
            f"🎮 Match Started!\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: {match_id}\n"
            f"Players:\n"
            f"1. {match['players'][0]['name']}\n"
            f"2. {match['players'][1]['name']}\n\n"
            f"Starting toss..."
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match_id}"),
                InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match_id}")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Try to update original message
        try:
            await context.bot.edit_message_text(
                chat_id=match["chat_id"],
                message_id=match["message_id"],
                text=update_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
    else:
        await update.message.reply_text(
            "Cannot join match! It might be full or private.",
            parse_mode=ParseMode.MARKDOWN
        )

async def matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /matches command"""
    chat = update.effective_chat
    match_ids = group_matches.get(str(chat.id), [])
    
    if not match_ids:
        await update.message.reply_text(
            "No active matches in this group.\nStart one with /play!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    matches_text = f"Active Matches ({len(match_ids)}):\n━━━━━━━━━━━━━━━━━━\n\n"
    
    for i, match_id in enumerate(match_ids[:5]):
        match = get_match(match_id)
        if match:
            status = "⏳ Waiting" if len(match["players"]) < 2 else "🎮 Playing"
            players = ", ".join([p["name"] for p in match["players"]]) or "Waiting..."
            
            matches_text += (
                f"{i+1}. {match_id} - {status}\n"
                f"   Players: {players}\n\n"
            )
    
    # Quick join buttons
    keyboard = []
    for match_id in match_ids[:3]:
        match = get_match(match_id)
        if match and len(match["players"]) < 2:
            keyboard.append([
                InlineKeyboardButton(f"🎮 JOIN {match_id}", callback_data=f"join_{match_id}")
            ])
    
    if keyboard:
        keyboard.append([
            InlineKeyboardButton("🔄 REFRESH", callback_data="refresh_matches"),
            InlineKeyboardButton("🎮 NEW MATCH", callback_data="new_match")
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
    """Handle /cancel command"""
    user = update.effective_user
    args = context.args
    
    # If match ID provided
    if args:
        match_id = args[0].upper()
        match = get_match(match_id)
        
        if not match:
            await update.message.reply_text(
                f"Match {match_id} not found.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if user can cancel
        user_in_match = any(str(p["id"]) == str(user.id) for p in match.get("players", []))
        if not user_in_match and str(user.id) != match["created_by"]:
            await update.message.reply_text(
                "You cannot cancel this match.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Cancel the match
        remove_match(match_id)
        await update.message.reply_text(
            f"✅ Match {match_id} cancelled.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # No match ID - cancel user's current match
    match = get_user_match(user.id)
    
    if not match:
        # Try to find match created by user
        for m in active_matches.values():
            if m["created_by"] == str(user.id) and len(m["players"]) < 2:
                match = m
                break
        
        if not match:
            await update.message.reply_text(
                "You are not in any match.\n"
                "To cancel specific match: /cancel MATCHID",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    # Cancel the match
    remove_match(match["match_id"])
    await update.message.reply_text(
        f"✅ Match {match['match_id']} cancelled.",
        parse_mode=ParseMode.MARKDOWN
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user = update.effective_user
    args = context.args
    
    # ADMIN STATS (in PM)
    if user.id == ADMIN_ID and update.effective_chat.type == ChatType.PRIVATE:
        total_users = len(set([p["id"] for match in active_matches.values() for p in match.get("players", [])]))
        total_groups = len(group_matches)
        total_matches = len(active_matches)
        
        admin_text = (
            f"🤖 ADMIN DASHBOARD\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Total Active Users: {total_users}\n"
            f"Total Groups: {total_groups}\n"
            f"Active Matches: {total_matches}\n\n"
            f"Group List:\n"
        )
        
        for chat_id, matches in group_matches.items()[:5]:  # Show first 5
            admin_text += f"• Group: {chat_id} - {len(matches)} matches\n"
        
        if total_groups > 5:
            admin_text += f"• ... and {total_groups - 5} more groups\n"
        
        await update.message.reply_text(
            admin_text,
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # PUBLIC STATS
    if args:
        # Check if username or ID provided
        target = args[0]
        # For now, show user's own stats
        pass
    
    stats_text = (
        f"📊 Player Statistics\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Player: {user.first_name}\n"
        f"Matches: 0\n"
        f"Wins: 0\n"
        f"Losses: 0\n\n"
        f"Statistics tracking coming soon!"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 REFRESH", callback_data=f"refresh_stats_{user.id}"),
            InlineKeyboardButton("📈 LEADERBOARD", callback_data="leaderboard")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        stats_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# ================= CALLBACK HANDLER =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries"""
    query = update.callback_query
    user = update.effective_user
    data = query.data
    
    await query.answer()
    
    if data.startswith("join_"):
        await handle_join_callback(query, context, data[5:], user)
    elif data.startswith("accept_"):
        await handle_accept_callback(query, context, data[7:], user)
    elif data.startswith("cancel_"):
        await handle_cancel_callback(query, context, data[7:], user)
    elif data == "play_now":
        await play_command(update, context)
    elif data == "my_stats":
        await stats_command(update, context)
    elif data == "refresh_matches":
        await matches_command(update, context)
    elif data == "new_match":
        await play_command(update, context)
    elif data == "leaderboard":
        await query.edit_message_text(
            "🏆 Leaderboard coming soon!",
            parse_mode=ParseMode.MARKDOWN
        )
    elif data.startswith("heads_") or data.startswith("tails_"):
        await handle_toss_callback(query, context, data, user)
    else:
        await query.answer("Coming soon!", show_alert=True)

async def handle_join_callback(query, context, match_id, user):
    """Handle join button callback"""
    # Check if already in match
    if str(user.id) in user_matches:
        await query.answer("You are already in a match!", show_alert=True)
        return
    
    # Get match
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    # Check if private
    if match["is_private"] and match["invited_user"]:
        if user.username and user.username.lower() != match["invited_user"].lower():
            await query.answer(f"Private match! Only @{match['invited_user']} can join.", show_alert=True)
            return
    
    # Create user data
    user_data = {
        "id": str(user.id),
        "name": user.first_name,
        "username": user.username
    }
    
    # Join match
    if join_match(match_id, user_data):
        match["state"] = "toss"
        
        await query.answer("✅ Joined successfully!")
        
        # Update message
        update_text = (
            f"🎮 Match Started!\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Match ID: {match_id}\n"
            f"Players:\n"
            f"1. {match['players'][0]['name']}\n"
            f"2. {match['players'][1]['name']}\n\n"
            f"Starting toss..."
        )
        
        keyboard = [
            [
                InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match_id}"),
                InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match_id}")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            update_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.answer("Cannot join match!", show_alert=True)

async def handle_accept_callback(query, context, match_id, user):
    """Handle accept challenge callback"""
    # Similar to join but verify it's the invited user
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
    """Handle cancel button callback"""
    match = get_match(match_id)
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    # Check if user can cancel
    user_in_match = any(str(p["id"]) == str(user.id) for p in match.get("players", []))
    if not user_in_match and str(user.id) != match["created_by"]:
        await query.answer("You cannot cancel this match!", show_alert=True)
        return
    
    remove_match(match_id)
    await query.answer("Match cancelled!")
    
    await query.edit_message_text(
        f"❌ Match Cancelled\n\n"
        f"Match ID: {match_id}\n"
        f"Cancelled by: {user.first_name}",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_toss_callback(query, context, data, user):
    """Handle toss callback"""
    match_id = data[6:]  # Remove "heads_" or "tails_"
    match = get_match(match_id)
    
    if not match:
        await query.answer("Match not found!", show_alert=True)
        return
    
    # Check if user is in this match
    if not any(str(p["id"]) == str(user.id) for p in match.get("players", [])):
        await query.answer("You are not in this match!", show_alert=True)
        return
    
    # Simulate toss
    toss_result = random.choice(["heads", "tails"])
    user_call = "heads" if data.startswith("heads_") else "tails"
    
    if user_call == toss_result:
        winner = user
        result_text = f"🎉 {user.first_name} won the toss!"
    else:
        # Other player wins
        other_player = [p for p in match["players"] if str(p["id"]) != str(user.id)][0]
        winner = other_player
        result_text = f"🎉 {other_player['name']} won the toss!"
    
    match["state"] = "inning1"
    match["toss_winner"] = winner
    
    update_text = (
        f"🪙 Toss Result\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Match ID: {match_id}\n"
        f"Your call: {user_call.upper()}\n"
        f"Result: {toss_result.upper()}\n\n"
        f"{result_text}\n\n"
        f"Match starting..."
    )
    
    # Game buttons
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
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        update_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    await query.answer(f"Toss result: {toss_result.upper()}!")

# ================= CLEANUP TASK =================
async def cleanup_task():
    """Background cleanup task"""
    while True:
        try:
            expired = cleanup_expired_matches()
            if expired > 0:
                logger.info(f"🧹 Cleaned up {expired} expired matches")
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            await asyncio.sleep(60)

# ================= MAIN BOT =================
async def main():
    """Main bot function"""
    # Initialize database
    await init_db()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("play", play_command))
    application.add_handler(CommandHandler("challenge", challenge_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("matches", matches_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Start cleanup task
    asyncio.create_task(cleanup_task())
    
    # Start bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    logger.info("✅ APEX CRICKET BOT is running!")
    
    # Keep running
    while True:
        await asyncio.sleep(3600)

# ================= RUN BOTH SERVERS =================
if __name__ == "__main__":
    import threading
    from app import app
    
    # Start Flask in separate thread
    def run_flask():
        app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Telegram bot
    asyncio.run(main())
