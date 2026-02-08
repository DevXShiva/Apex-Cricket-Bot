import os
import json
import random
import asyncio
import uuid
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Set
from enum import Enum
from collections import defaultdict
import logging
from dataclasses import dataclass, field
from contextlib import asynccontextmanager

import pymongo
from bson import ObjectId
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    InputMediaPhoto,
    ChatPermissions
)
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    CallbackQueryHandler, 
    ContextTypes,
    MessageHandler,
    filters,
    CallbackContext
)
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError

# ================= CONFIGURATION =================
BOT3_TOKEN = os.getenv("BOT3_TOKEN", "")
MONGODB_URI = os.getenv("MONGODB_URI", "")

# ADMIN IDs
ADMIN_IDS = [5298223577]

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Emojis
EMOJIS = {
    "stadium": "🏟️", "trophy": "🏆", "bat": "🏏", "ball": "🎯",
    "fire": "🔥", "star": "⭐", "crown": "👑", "medal": "🏅",
    "chart": "📈", "team": "👥", "clock": "⏰", "calendar": "📅",
    "money": "💰", "rank": "🥇", "live": "🔴", "spectator": "👀",
    "achievement": "🎖️", "user": "👤", "group": "👥", "admin": "🛡️",
    "search": "🔍", "lock": "🔒", "key": "🔑", "hourglass": "⏳",
    "warning": "⚠️", "check": "✅", "cross": "❌", "sword": "⚔️"
}

DIVIDER = "✨━━━━━━━━━━━━━━━━━━✨"
FOOTER = "\n\n───\n🌟 **Powered by APEX CRICKET | @apexcricket_bot**"

# Match Configuration
MATCH_TIMEOUT = 120  # 2 minutes timeout for inactive matches
MAX_MATCHES_PER_GROUP = 10  # Maximum concurrent matches per group
CLEANUP_INTERVAL = 30  # Cleanup every 30 seconds

# MongoDB Collections
DB_NAME = "apex_cricket_multi"
MATCHES_COLLECTION = "completed_matches"
PLAYER_STATS_COLLECTION = "player_stats"
ACTIVE_MATCHES_COLLECTION = "active_matches_temp"  # For recovery only

# MongoDB Client
mongo_client = None
db = None

# ================= DATA STRUCTURES =================
@dataclass
class Player:
    """Player information"""
    user_id: str
    username: Optional[str]
    first_name: str
    is_cpu: bool = False
    cpu_difficulty: str = "medium"
    joined_at: datetime = field(default_factory=datetime.utcnow)

@dataclass
class Match:
    """Active match data structure"""
    match_id: str
    chat_id: str
    players: List[Player] = field(default_factory=list)
    allowed_players: Set[str] = field(default_factory=set)  # For private challenges
    is_private: bool = False
    created_by: str = None  # User who created the match
    invited_user_id: str = None  # For private challenges
    
    # Game state
    score: int = 0
    wickets: int = 0
    overs: int = 0
    balls: int = 0
    target: Optional[int] = None
    current_batsman: Optional[str] = None
    current_bowler: Optional[str] = None
    choices: Dict[str, int] = field(default_factory=dict)
    history: List[str] = field(default_factory=list)
    
    # Match state
    state: str = "waiting"  # waiting, toss, inning1, inning2, completed
    toss_caller: Optional[str] = None
    toss_winner: Optional[str] = None
    bat_first: Optional[str] = None
    bowl_first: Optional[str] = None
    
    # Timing
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    
    # Message IDs for updates
    message_id: Optional[int] = None
    
    def is_expired(self) -> bool:
        """Check if match is expired (inactive for 2 minutes)"""
        return (datetime.utcnow() - self.last_activity).total_seconds() > MATCH_TIMEOUT
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.utcnow()
    
    def add_player(self, player: Player) -> bool:
        """Add player to match"""
        if len(self.players) >= 2:
            return False
        
        # For private matches, check if player is allowed
        if self.is_private and str(player.user_id) not in self.allowed_players:
            return False
        
        self.players.append(player)
        self.update_activity()
        return True
    
    def get_player(self, user_id: str) -> Optional[Player]:
        """Get player by user_id"""
        for player in self.players:
            if str(player.user_id) == str(user_id):
                return player
        return None
    
    def get_opponent(self, user_id: str) -> Optional[Player]:
        """Get opponent player"""
        for player in self.players:
            if str(player.user_id) != str(user_id):
                return player
        return None
    
    def is_player_in_match(self, user_id: str) -> bool:
        """Check if user is in this match"""
        return any(str(p.user_id) == str(user_id) for p in self.players)

# ================= MATCH MANAGER =================
class MatchManager:
    """Manages all active matches"""
    
    def __init__(self):
        self.active_matches: Dict[str, Match] = {}  # match_id -> Match
        self.user_to_match: Dict[str, str] = {}  # user_id -> match_id
        self.group_matches: Dict[str, List[str]] = defaultdict(list)  # chat_id -> [match_ids]
        self.private_invites: Dict[str, str] = {}  # invited_user_id -> match_id
        
    def create_match(self, chat_id: str, created_by: str, is_private: bool = False, 
                    invited_user_id: str = None) -> Match:
        """Create a new match"""
        # Check if group has too many matches
        if len(self.group_matches.get(str(chat_id), [])) >= MAX_MATCHES_PER_GROUP:
            return None
        
        # Generate unique match ID
        match_id = str(uuid.uuid4())[:8].upper()
        
        # Create match
        match = Match(
            match_id=match_id,
            chat_id=str(chat_id),
            created_by=created_by,
            is_private=is_private,
            invited_user_id=invited_user_id
        )
        
        # Add creator to allowed players
        if is_private:
            match.allowed_players.add(created_by)
            if invited_user_id:
                match.allowed_players.add(str(invited_user_id))
        
        # Store match
        self.active_matches[match_id] = match
        self.user_to_match[created_by] = match_id
        
        # Add to group matches
        if str(chat_id) not in self.group_matches:
            self.group_matches[str(chat_id)] = []
        self.group_matches[str(chat_id)].append(match_id)
        
        # Store private invite
        if is_private and invited_user_id:
            self.private_invites[str(invited_user_id)] = match_id
        
        logger.info(f"🎮 Match created: {match_id} in chat {chat_id}")
        return match
    
    def get_match(self, match_id: str) -> Optional[Match]:
        """Get match by ID"""
        return self.active_matches.get(match_id)
    
    def get_match_by_chat(self, chat_id: str) -> Optional[Match]:
        """Get any match in chat (for /cancel without ID)"""
        match_ids = self.group_matches.get(str(chat_id), [])
        if match_ids:
            return self.active_matches.get(match_ids[0])
        return None
    
    def get_user_match(self, user_id: str) -> Optional[Match]:
        """Get match where user is playing"""
        match_id = self.user_to_match.get(str(user_id))
        if match_id:
            return self.active_matches.get(match_id)
        return None
    
    def join_match(self, match_id: str, player: Player) -> bool:
        """Join an existing match"""
        match = self.get_match(match_id)
        if not match:
            return False
        
        # Check if match is full
        if len(match.players) >= 2:
            return False
        
        # Check if player already in a match
        if str(player.user_id) in self.user_to_match:
            return False
        
        # Try to add player
        if match.add_player(player):
            self.user_to_match[str(player.user_id)] = match_id
            logger.info(f"👤 Player {player.user_id} joined match {match_id}")
            return True
        
        return False
    
    def complete_match(self, match_id: str):
        """Complete and remove a match"""
        match = self.get_match(match_id)
        if not match:
            return
        
        # Remove from user_to_match
        for player in match.players:
            user_id = str(player.user_id)
            if user_id in self.user_to_match:
                del self.user_to_match[user_id]
        
        # Remove from group matches
        chat_id = match.chat_id
        if chat_id in self.group_matches and match_id in self.group_matches[chat_id]:
            self.group_matches[chat_id].remove(match_id)
        
        # Remove from private invites
        for user_id, mid in list(self.private_invites.items()):
            if mid == match_id:
                del self.private_invites[user_id]
        
        # Remove match
        if match_id in self.active_matches:
            del self.active_matches[match_id]
        
        logger.info(f"🏁 Match completed: {match_id}")
    
    def cleanup_expired_matches(self):
        """Clean up expired matches"""
        expired_matches = []
        current_time = datetime.utcnow()
        
        for match_id, match in list(self.active_matches.items()):
            if match.is_expired():
                expired_matches.append(match_id)
        
        for match_id in expired_matches:
            match = self.active_matches.get(match_id)
            if match:
                logger.info(f"🧹 Cleaning expired match: {match_id}")
                self.complete_match(match_id)
        
        return len(expired_matches)
    
    def get_group_match_count(self, chat_id: str) -> int:
        """Get number of active matches in group"""
        return len(self.group_matches.get(str(chat_id), []))
    
    def can_create_match(self, chat_id: str) -> bool:
        """Check if new match can be created in group"""
        return self.get_group_match_count(chat_id) < MAX_MATCHES_PER_GROUP

# Global match manager instance
match_manager = MatchManager()

# ================= DATABASE =================
async def init_db():
    """Initialize MongoDB"""
    global mongo_client, db
    try:
        mongo_client = pymongo.MongoClient(MONGODB_URI)
        db = mongo_client[DB_NAME]
        logger.info("✅ MongoDB connected")
        
        # Create indexes
        db[MATCHES_COLLECTION].create_index([("match_id", 1)], unique=True)
        db[MATCHES_COLLECTION].create_index([("players.user_id", 1)])
        db[PLAYER_STATS_COLLECTION].create_index([("user_id", 1)], unique=True)
        
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        raise

async def save_completed_match(match_data: Dict):
    """Save completed match to database"""
    try:
        match_data["saved_at"] = datetime.utcnow()
        db[MATCHES_COLLECTION].insert_one(match_data)
        logger.info(f"💾 Saved completed match: {match_data.get('match_id')}")
    except Exception as e:
        logger.error(f"❌ Error saving match: {e}")

# ================= COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    chat = update.effective_chat
    
    welcome_msg = (
        f"{EMOJIS['stadium']} *🏏 APEX CRICKET MULTI-MATCH* 🏏\n"
        f"{DIVIDER}\n\n"
        
        f"✨ *NEW FEATURES:*\n"
        f"• Multiple matches in same group ✅\n"
        f"• Private challenges 🔒\n"
        f"• Auto-cleanup after 2 mins 🧹\n\n"
        
        f"🚀 *QUICK COMMANDS:*\n"
        f"• /play - Start a match (solo/private)\n"
        f"• /challenge @username - Challenge friend\n"
        f"• /join MATCH_ID - Join match\n"
        f"• /matches - View active matches\n"
        f"• /cancel - Cancel your match\n"
        f"• /stats - View statistics\n\n"
        
        f"🎯 *Up to {MAX_MATCHES_PER_GROUP} matches simultaneously in this group!*"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🎮 QUICK PLAY", callback_data="quick_play"),
            InlineKeyboardButton("🔒 CHALLENGE", callback_data="challenge_menu")
        ],
        [
            InlineKeyboardButton("👀 VIEW MATCHES", callback_data="view_matches"),
            InlineKeyboardButton("📊 STATS", callback_data="show_stats")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        welcome_msg + FOOTER,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /play command - Start a match"""
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    # Check if user already in a match
    existing_match = match_manager.get_user_match(str(user.id))
    if existing_match:
        await update.message.reply_text(
            f"⚠️ *You are already in a match!*\n\n"
            f"Match ID: `{existing_match.match_id}`\n"
            f"Status: `{existing_match.state}`\n\n"
            f"Use `/cancel` to leave current match first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check if group can have more matches
    if not match_manager.can_create_match(chat.id):
        await update.message.reply_text(
            f"⚠️ *Maximum matches reached in this group!*\n\n"
            f"Only {MAX_MATCHES_PER_GROUP} matches can run simultaneously.\n"
            f"Wait for a match to finish or use `/cancel` on an inactive match.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check for private challenge
    is_private = False
    invited_user_id = None
    
    if args and args[0].startswith('@'):
        # This is handled by /challenge command
        await update.message.reply_text(
            f"🎯 Use `/challenge {args[0]}` to challenge a friend!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Create public match
    match = match_manager.create_match(
        chat_id=chat.id,
        created_by=str(user.id),
        is_private=False
    )
    
    if not match:
        await update.message.reply_text(
            "❌ *Could not create match!* Try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Add creator as player
    creator_player = Player(
        user_id=str(user.id),
        username=user.username,
        first_name=user.first_name
    )
    match.add_player(creator_player)
    match.state = "waiting"
    
    # Create match message
    match_msg = (
        f"{EMOJIS['sword']} *NEW MATCH CREATED!*\n"
        f"{DIVIDER}\n\n"
        
        f"🎮 *Match ID:* `{match.match_id}`\n"
        f"👤 *Created by:* {user.first_name}\n"
        f"👥 *Type:* Public Match\n"
        f"⏳ *Timeout:* 2 minutes\n\n"
        
        f"🔹 *Players (1/2):*\n"
        f"1. {user.first_name}\n"
        f"2. Waiting for opponent...\n\n"
        
        f"🎯 *To join:*\n"
        f"• Use `/join {match.match_id}`\n"
        f"• Or click button below\n\n"
        
        f"⏰ Match will auto-cancel in 2 minutes if no one joins."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🎮 JOIN MATCH", callback_data=f"join_{match.match_id}"),
            InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_{match.match_id}")
        ],
        [
            InlineKeyboardButton("🤖 VS CPU", callback_data=f"cpu_{match.match_id}")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        match_msg + FOOTER,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Store message ID
    match.message_id = message.message_id

async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /challenge command - Challenge specific friend"""
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    if not args:
        await update.message.reply_text(
            f"🎯 *Challenge a friend!*\n\n"
            f"*Usage:* `/challenge @username`\n"
            f"*Example:* `/challenge @john`\n\n"
            f"Only the mentioned user can join your match!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check if user already in a match
    existing_match = match_manager.get_user_match(str(user.id))
    if existing_match:
        await update.message.reply_text(
            f"⚠️ *You are already in a match!*\n\n"
            f"Match ID: `{existing_match.match_id}`\n"
            f"Use `/cancel` to leave current match first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check if group can have more matches
    if not match_manager.can_create_match(chat.id):
        await update.message.reply_text(
            f"⚠️ *Maximum matches reached!*\n"
            f"Wait for a match to finish.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Get mentioned username
    username = args[0]
    if not username.startswith('@'):
        username = f"@{username}"
    
    # In real implementation, you'd need to get user_id from username
    # For now, we'll create a placeholder
    invited_username = username[1:]  # Remove @
    
    # Create private match
    match = match_manager.create_match(
        chat_id=chat.id,
        created_by=str(user.id),
        is_private=True,
        invited_user_id=invited_username  # Note: This should be user_id in real implementation
    )
    
    if not match:
        await update.message.reply_text(
            "❌ *Could not create challenge!* Try again later.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Add creator as player
    creator_player = Player(
        user_id=str(user.id),
        username=user.username,
        first_name=user.first_name
    )
    match.add_player(creator_player)
    match.state = "waiting"
    
    # Create challenge message
    challenge_msg = (
        f"{EMOJIS['lock']} *PRIVATE CHALLENGE!* {EMOJIS['lock']}\n"
        f"{DIVIDER}\n\n"
        
        f"⚔️ *Challenge from:* {user.first_name}\n"
        f"🎯 *Challenged:* {username}\n"
        f"🔒 *Match ID:* `{match.match_id}`\n\n"
        
        f"🔹 *Special Rules:*\n"
        f"• Only {username} can join this match\n"
        f"• Others cannot join or spectate\n"
        f"• Auto-cancels in 2 minutes\n\n"
        
        f"{username}, click below to accept challenge!"
    )
    
    keyboard = [
        [
            InlineKeyboardButton(f"✅ ACCEPT CHALLENGE", 
                               callback_data=f"accept_{match.match_id}"),
            InlineKeyboardButton("❌ DECLINE", 
                               callback_data=f"decline_{match.match_id}")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = await update.message.reply_text(
        challenge_msg + FOOTER,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Store message ID
    match.message_id = message.message_id

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /join command - Join a match"""
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    if not args:
        # Show active matches
        await show_active_matches(update, context)
        return
    
    match_id = args[0].upper()
    
    # Check if user already in a match
    existing_match = match_manager.get_user_match(str(user.id))
    if existing_match:
        await update.message.reply_text(
            f"⚠️ *You are already in a match!*\n\n"
            f"Match ID: `{existing_match.match_id}`\n"
            f"Use `/cancel` to leave current match first.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Get the match
    match = match_manager.get_match(match_id)
    if not match:
        await update.message.reply_text(
            f"❌ *Match not found!*\n\n"
            f"Match ID `{match_id}` doesn't exist or has expired.\n"
            f"Use `/matches` to see active matches.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check if match is in same chat
    if str(match.chat_id) != str(chat.id):
        await update.message.reply_text(
            f"❌ *Wrong group!*\n\n"
            f"This match belongs to another group.\n"
            f"You can only join matches in this group.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Check if match is private
    if match.is_private:
        invited_id = match.invited_user_id
        # In real implementation, compare user_id
        # For demo, we'll check username
        if user.username and user.username.lower() != invited_id.lower():
            await update.message.reply_text(
                f"🔒 *PRIVATE MATCH!*\n\n"
                f"This match is a private challenge.\n"
                f"Only {invited_id} can join this match.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    # Create player object
    player = Player(
        user_id=str(user.id),
        username=user.username,
        first_name=user.first_name
    )
    
    # Try to join
    if match_manager.join_match(match_id, player):
        # Update match state
        match.state = "toss"
        match.toss_caller = str(user.id)  # Let joiner call toss
        
        # Update match message
        await update_match_message(update, context, match)
        
        await update.message.reply_text(
            f"✅ *Joined successfully!*\n\n"
            f"Match ID: `{match_id}`\n"
            f"Opponent: {match.players[0].first_name}\n\n"
            f"Match starting...",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"❌ *Cannot join match!*\n\n"
            f"Possible reasons:\n"
            f"• Match is full\n"
            f"• You're already in another match\n"
            f"• Private match restrictions",
            parse_mode=ParseMode.MARKDOWN
        )

async def matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /matches command - Show active matches"""
    await show_active_matches(update, context)

async def show_active_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all active matches in the group"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Get matches in this group
    match_ids = match_manager.group_matches.get(str(chat.id), [])
    
    if not match_ids:
        await update.message.reply_text(
            f"{EMOJIS['stadium']} *NO ACTIVE MATCHES*\n\n"
            f"No matches are currently active in this group.\n"
            f"Start one with `/play`!",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    matches_list = []
    for i, match_id in enumerate(match_ids[:10]):  # Show max 10
        match = match_manager.get_match(match_id)
        if match:
            status_emoji = "🟢" if len(match.players) < 2 else "🟡"
            lock_emoji = "🔒" if match.is_private else "🔓"
            
            player_names = [p.first_name for p in match.players]
            players_text = ", ".join(player_names) if player_names else "Waiting..."
            
            time_ago = int((datetime.utcnow() - match.last_activity).total_seconds())
            time_left = max(0, MATCH_TIMEOUT - time_ago)
            
            matches_list.append(
                f"{i+1}. {status_emoji} {lock_emoji} `{match_id}`\n"
                f"   👥 {players_text}\n"
                f"   ⏳ {time_left}s left\n"
                f"   🎮 State: {match.state}"
            )
    
    matches_msg = (
        f"{EMOJIS['stadium']} *ACTIVE MATCHES*\n"
        f"{DIVIDER}\n\n"
        
        f"📊 *Total:* {len(match_ids)}/{MAX_MATCHES_PER_GROUP}\n\n"
    )
    
    if matches_list:
        matches_msg += "\n".join(matches_list)
        matches_msg += f"\n\n🎯 *Join with:* `/join MATCH_ID`"
    else:
        matches_msg += "No active matches found."
    
    keyboard = []
    for match_id in match_ids[:3]:  # Quick join buttons for first 3
        match = match_manager.get_match(match_id)
        if match and len(match.players) < 2:
            keyboard.append([
                InlineKeyboardButton(f"🎮 JOIN {match_id[:4]}...", 
                                   callback_data=f"join_{match_id}")
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
            matches_msg + FOOTER,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            matches_msg + FOOTER,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command"""
    user = update.effective_user
    chat = update.effective_chat
    args = context.args
    
    # If match ID provided
    if args:
        match_id = args[0].upper()
        match = match_manager.get_match(match_id)
        
        if not match:
            await update.message.reply_text(
                f"❌ *Match not found!*\n"
                f"Match ID `{match_id}` doesn't exist.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if user is in the match
        if not match.is_player_in_match(str(user.id)) and str(user.id) != match.created_by:
            await update.message.reply_text(
                f"⚠️ *Cannot cancel!*\n"
                f"You are not in this match.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Cancel the match
        await cancel_match_by_id(update, context, match_id, user)
        return
    
    # No match ID - cancel user's current match
    match = match_manager.get_user_match(str(user.id))
    
    if not match:
        # Try to find match created by user
        for m in match_manager.active_matches.values():
            if m.created_by == str(user.id) and len(m.players) < 2:
                match = m
                break
        
        if not match:
            await update.message.reply_text(
                f"ℹ️ *No active match found!*\n"
                f"You are not in any match.\n\n"
                f"To cancel a specific match: `/cancel MATCH_ID`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
    
    # Cancel the match
    await cancel_match_by_id(update, context, match.match_id, user)

async def cancel_match_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                           match_id: str, user):
    """Cancel a specific match"""
    match = match_manager.get_match(match_id)
    
    if not match:
        return
    
    # Notify players
    player_names = [p.first_name for p in match.players]
    
    cancel_msg = (
        f"{EMOJIS['cross']} *MATCH CANCELLED*\n"
        f"{DIVIDER}\n\n"
        
        f"Match ID: `{match_id}`\n"
        f"Cancelled by: {user.first_name}\n"
        f"Players: {', '.join(player_names) if player_names else 'None'}\n\n"
        
        f"✅ Match removed from active matches."
    )
    
    # Try to update original match message
    try:
        if match.message_id:
            await context.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.message_id,
                text=cancel_msg + FOOTER,
                parse_mode=ParseMode.MARKDOWN
            )
    except:
        pass
    
    # Send cancellation message
    await update.message.reply_text(
        cancel_msg,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Remove match
    match_manager.complete_match(match_id)

# ================= MATCH MESSAGE UPDATER =================
async def update_match_message(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             match: Match):
    """Update the match message with current status"""
    try:
        if not match.message_id:
            return
        
        # Prepare message based on match state
        if match.state == "waiting":
            msg = await create_waiting_message(match)
        elif match.state == "toss":
            msg = await create_toss_message(match)
        elif match.state in ["inning1", "inning2"]:
            msg = await create_game_message(match)
        elif match.state == "completed":
            msg = await create_completed_message(match)
        else:
            return
        
        # Update message
        await context.bot.edit_message_text(
            chat_id=int(match.chat_id),
            message_id=match.message_id,
            text=msg + FOOTER,
            reply_markup=get_match_keyboard(match),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"❌ Error updating match message: {e}")

async def create_waiting_message(match: Match) -> str:
    """Create waiting message"""
    player_list = "\n".join([f"{i+1}. {p.first_name}" for i, p in enumerate(match.players)])
    
    return (
        f"{EMOJIS['hourglass']} *WAITING FOR PLAYERS*\n"
        f"{DIVIDER}\n\n"
        
        f"🎮 *Match ID:* `{match.match_id}`\n"
        f"🔒 *Type:* {'Private 🔒' if match.is_private else 'Public 🔓'}\n"
        f"⏳ *Timeout:* {MATCH_TIMEOUT}s\n\n"
        
        f"👥 *Players ({len(match.players)}/2):*\n{player_list}\n\n"
        
        f"🎯 *To join:* `/join {match.match_id}`\n\n"
        
        f"⏰ Match will auto-cancel in 2 minutes."
    )

async def create_toss_message(match: Match) -> str:
    """Create toss message"""
    toss_caller = match.get_player(match.toss_caller)
    opponent = match.get_opponent(match.toss_caller)
    
    return (
        f"{EMOJIS['coin']} *TOSS TIME!*\n"
        f"{DIVIDER}\n\n"
        
        f"🎮 *Match ID:* `{match.match_id}`\n\n"
        
        f"👥 *Players:*\n"
        f"1. {match.players[0].first_name}\n"
        f"2. {match.players[1].first_name}\n\n"
        
        f"🎯 *Toss Caller:* {toss_caller.first_name}\n\n"
        
        f"🪙 Call Heads or Tails!"
    )

async def create_game_message(match: Match) -> str:
    """Create in-game message"""
    batsman = match.get_player(match.current_batsman)
    bowler = match.get_player(match.current_bowler)
    
    # Format score
    score = f"{match.score}/{match.wickets}"
    overs = f"{match.overs}.{match.balls}"
    
    # Format history
    history = " ".join(match.history[-6:]) if match.history else "---"
    
    # Target info if chasing
    target_info = ""
    if match.state == "inning2" and match.target:
        needed = match.target - match.score
        balls_left = 6 - (match.overs * 6 + match.balls)
        target_info = f"\n🎯 *Target:* {match.target} | Need: {needed} in {balls_left} balls"
    
    return (
        f"{EMOJIS['live']} *MATCH IN PROGRESS*\n"
        f"{DIVIDER}\n\n"
        
        f"🎮 *Match ID:* `{match.match_id}`\n"
        f"🏏 *Inning:* {match.state[-1]}\n\n"
        
        f"🏏 *Batting:* {batsman.first_name if batsman else '---'}\n"
        f"🎯 *Bowling:* {bowler.first_name if bowler else '---'}\n\n"
        
        f"📊 *Score:* {score}\n"
        f"⏳ *Overs:* {overs}/1.0\n"
        f"📝 *Last balls:* {history}\n"
        
        f"{target_info}\n\n"
        
        f"🎮 Make your move!"
    )

async def create_completed_message(match: Match) -> str:
    """Create completed match message"""
    winner = match.get_player(match.winner) if hasattr(match, 'winner') else None
    loser = match.get_opponent(match.winner) if winner else None
    
    return (
        f"{EMOJIS['trophy']} *MATCH COMPLETED!*\n"
        f"{DIVIDER}\n\n"
        
        f"🎮 *Match ID:* `{match.match_id}`\n\n"
        
        f"🏆 *Winner:* {winner.first_name if winner else 'Draw'}\n"
        f"🎯 *Score:* {match.score}/{match.wickets}\n\n"
        
        f"👥 *Players:*\n"
        f"1. {match.players[0].first_name}\n"
        f"2. {match.players[1].first_name}\n\n"
        
        f"⏰ *Duration:* {int((match.completed_at - match.created_at).total_seconds())}s"
    )

def get_match_keyboard(match: Match) -> InlineKeyboardMarkup:
    """Get appropriate keyboard for match state"""
    if match.state == "waiting":
        keyboard = [
            [
                InlineKeyboardButton("🎮 JOIN", callback_data=f"join_{match.match_id}"),
                InlineKeyboardButton("❌ CANCEL", callback_data=f"cancel_{match.match_id}")
            ],
            [
                InlineKeyboardButton("🤖 VS CPU", callback_data=f"cpu_{match.match_id}")
            ]
        ]
    elif match.state == "toss":
        keyboard = [
            [
                InlineKeyboardButton("🌕 HEADS", callback_data=f"heads_{match.match_id}"),
                InlineKeyboardButton("🌑 TAILS", callback_data=f"tails_{match.match_id}")
            ]
        ]
    elif match.state in ["inning1", "inning2"]:
        keyboard = [
            [
                InlineKeyboardButton("1️⃣", callback_data=f"n1_{match.match_id}"),
                InlineKeyboardButton("2️⃣", callback_data=f"n2_{match.match_id}"),
                InlineKeyboardButton("3️⃣", callback_data=f"n3_{match.match_id}")
            ],
            [
                InlineKeyboardButton("4️⃣", callback_data=f"n4_{match.match_id}"),
                InlineKeyboardButton("5️⃣", callback_data=f"n5_{match.match_id}"),
                InlineKeyboardButton("6️⃣", callback_data=f"n6_{match.match_id}")
            ],
            [
                InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{match.match_id}")
            ]
        ]
    else:
        keyboard = []
    
    return InlineKeyboardMarkup(keyboard)

# ================= CALLBACK HANDLER =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    user = update.effective_user
    data = query.data
    
    await query.answer()
    
    # Handle different callback types
    if data.startswith("join_"):
        await handle_join_callback(query, context, data[5:], user)
    elif data.startswith("accept_"):
        await handle_accept_callback(query, context, data[7:], user)
    elif data.startswith("cancel_"):
        await handle_cancel_callback(query, context, data[7:], user)
    elif data.startswith("cpu_"):
        await handle_cpu_callback(query, context, data[4:], user)
    elif data == "refresh_matches":
        await show_active_matches(update, context)
    elif data == "new_match":
        await play_command(update, context)
    elif data == "challenge_menu":
        await query.edit_message_text(
            f"🔒 *PRIVATE CHALLENGE*\n\n"
            f"Challenge a specific friend!\n\n"
            f"*Usage:* `/challenge @username`\n\n"
            f"Only they can join your match!",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        # Game-related callbacks (toss, numbers, etc.)
        await handle_game_callback(query, context, data, user)

async def handle_join_callback(query, context, match_id, user):
    """Handle join button callback"""
    # Check if user already in a match
    existing_match = match_manager.get_user_match(str(user.id))
    if existing_match:
        await query.answer("⚠️ You are already in a match!", show_alert=True)
        return
    
    # Get match
    match = match_manager.get_match(match_id)
    if not match:
        await query.answer("❌ Match not found!", show_alert=True)
        return
    
    # Check if match is private
    if match.is_private:
        invited_id = match.invited_user_id
        # Check if this user is the invited one
        if user.username and user.username.lower() != invited_id.lower():
            await query.answer(f"🔒 Private match! Only {invited_id} can join.", show_alert=True)
            return
    
    # Create player and join
    player = Player(
        user_id=str(user.id),
        username=user.username,
        first_name=user.first_name
    )
    
    if match_manager.join_match(match_id, player):
        match.state = "toss"
        match.toss_caller = str(user.id)  # Let joiner call toss
        
        await update_match_message(update, context, match)
        await query.answer("✅ Joined successfully!")
    else:
        await query.answer("❌ Cannot join match!", show_alert=True)

async def handle_accept_callback(query, context, match_id, user):
    """Handle accept challenge callback"""
    # Similar to join but with extra checks for private match
    await handle_join_callback(query, context, match_id, user)

# ================= CLEANUP DAEMON =================
async def cleanup_daemon():
    """Background task to cleanup expired matches"""
    while True:
        try:
            expired_count = match_manager.cleanup_expired_matches()
            if expired_count > 0:
                logger.info(f"🧹 Cleaned up {expired_count} expired matches")
            
            # Also cleanup old cache entries
            current_time = time.time()
            # You can add more cleanup logic here
            
            await asyncio.sleep(CLEANUP_INTERVAL)
            
        except Exception as e:
            logger.error(f"❌ Error in cleanup daemon: {e}")
            await asyncio.sleep(CLEANUP_INTERVAL)

# ================= BOT STARTUP =================
async def start_bot():
    """Start the bot"""
    try:
        # Initialize database
        await init_db()
        
        # Create application
        application = ApplicationBuilder().token(BOT3_TOKEN).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("play", play_command))
        application.add_handler(CommandHandler("challenge", challenge_command))
        application.add_handler(CommandHandler("join", join_command))
        application.add_handler(CommandHandler("matches", matches_command))
        application.add_handler(CommandHandler("cancel", cancel_command))
        application.add_handler(CommandHandler("stats", stats_command))
        
        # Add callback handler
        application.add_handler(CallbackQueryHandler(handle_callback))
        
        # Start the bot
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        logger.info("✅ APEX CRICKET MULTI-MATCH BOT is running!")
        
        # Start cleanup daemon
        asyncio.create_task(cleanup_daemon())
        
        # Keep running
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        logger.error(f"❌ Bot startup failed: {e}")
        raise

# ================= STUB FUNCTIONS =================
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stub for stats command"""
    await update.message.reply_text(
        f"{EMOJIS['chart']} *STATISTICS*\n\n"
        f"Coming soon!\n\n"
        f"Track your wins, runs, and achievements!",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_game_callback(query, context, data, user):
    """Stub for game callbacks"""
    await query.answer("🎮 Game logic coming soon!", show_alert=True)

# ================= MAIN =================
if __name__ == "__main__":
    asyncio.run(start_bot())
