import os
import random
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DIVIDER = "◈───────────────────◈"
FOOTER = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

# ================= MONGODB SETUP =================
client = AsyncIOMotorClient(MONGODB_URI)
db = client["apex_cricket_bot"]  # Bot-specific database

# Collections
users_collection = db["users"]
matches_collection = db["matches"]

# In-memory cache for active matches
matches_cache = {}

# ================= MONGODB HELPER FUNCTIONS =================
async def get_or_create_user(user_id: int, name: str):
    """Get existing user or create new"""
    user = await users_collection.find_one({"user_id": str(user_id)})
    
    if not user:
        user_data = {
            "user_id": str(user_id),
            "name": name,
            "first_seen": datetime.now(),
            "total_matches": 0,
            "wins": 0,
            "total_runs": 0
        }
        await users_collection.insert_one(user_data)
        return user_data
    
    return user

async def update_user_stats(user_id: int, won: bool = False, runs: int = 0):
    """Update user statistics after match"""
    update_data = {
        "$inc": {
            "total_matches": 1,
            "total_runs": runs
        }
    }
    
    if won:
        update_data["$inc"]["wins"] = 1
    
    await users_collection.update_one(
        {"user_id": str(user_id)},
        update_data,
        upsert=True
    )

async def save_match_to_db(match_data: dict):
    """Save completed match to database"""
    await matches_collection.insert_one(match_data)

async def get_leaderboard(limit: int = 10):
    """Get top players leaderboard"""
    pipeline = [
        {
            "$match": {
                "total_matches": {"$gt": 0}
            }
        },
        {
            "$project": {
                "name": 1,
                "wins": 1,
                "total_matches": 1,
                "win_rate": {
                    "$multiply": [
                        {"$divide": ["$wins", "$total_matches"]},
                        100
                    ]
                },
                "avg_runs": {
                    "$cond": [
                        {"$eq": ["$total_matches", 0]},
                        0,
                        {"$divide": ["$total_runs", "$total_matches"]}
                    ]
                }
            }
        },
        {"$sort": {"wins": -1, "win_rate": -1}},
        {"$limit": limit}
    ]
    
    cursor = users_collection.aggregate(pipeline)
    return await cursor.to_list(length=limit)

# ================= BOT COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    # Ensure user exists in database
    await get_or_create_user(user.id, user.first_name)
    
    welcome_text = f"""
{DIVIDER}
        🏏 **APEX CRICKET WORLD**
{DIVIDER}

Welcome {user.first_name}! 

⚡ **Features:**
• 🤖 Play vs CPU
• 👥 Play with friends
• 📊 Track your stats
• 🏆 Leaderboard

Choose your game mode:
    """
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 VS CPU", callback_data=f"mode_cpu_{update.effective_chat.id}"),
         InlineKeyboardButton("👥 VS FRIEND", callback_data=f"mode_duel_{update.effective_chat.id}")],
        [InlineKeyboardButton("📊 LEADERBOARD", callback_data=f"show_lb_{update.effective_chat.id}")]
    ])
    
    await update.message.reply_text(welcome_text + FOOTER, 
                                   reply_markup=keyboard, 
                                   parse_mode=ParseMode.MARKDOWN)

async def start_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cricket command - same as start"""
    await start_command(update, context)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user = update.effective_user
    user_data = await get_or_create_user(user.id, user.first_name)
    
    stats_text = f"""
{DIVIDER}
📊 **YOUR STATISTICS**
{DIVIDER}

👤 **Player:** {user_data['name']}
🎮 **Matches Played:** {user_data.get('total_matches', 0)}
🏆 **Matches Won:** {user_data.get('wins', 0)}
📈 **Win Rate:** {user_data.get('total_matches', 0) and (user_data.get('wins', 0)/user_data.get('total_matches', 1)*100):.1f}%
🏏 **Total Runs:** {user_data.get('total_runs', 0)}
⚡ **Avg Runs/Match:** {user_data.get('total_matches', 0) and (user_data.get('total_runs', 0)/user_data.get('total_matches', 1)):.1f}
    """
    
    await update.message.reply_text(stats_text + FOOTER, parse_mode=ParseMode.MARKDOWN)

# ================= GAME LOGIC FUNCTIONS =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    data = query.data
    parts = data.split('_')
    
    chat_id = str(update.effective_chat.id)
    
    # Leaderboard display
    if parts[0] == "show" and parts[1] == "lb":
        leaderboard = await get_leaderboard(10)
        
        lb_text = f"{DIVIDER}\n🏆 **TOP 10 PLAYERS**\n{DIVIDER}\n\n"
        
        if not leaderboard:
            lb_text += "No records yet. Be the first to play!"
        else:
            for i, player in enumerate(leaderboard, 1):
                win_rate = player.get('win_rate', 0)
                lb_text += f"{i}. {player['name']} - {player.get('wins', 0)} wins ({win_rate:.1f}%)\n"
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Menu", callback_data=f"back_{chat_id}")]
        ])
        
        await query.edit_message_text(lb_text + FOOTER, 
                                     reply_markup=keyboard, 
                                     parse_mode=ParseMode.MARKDOWN)
        return
    
    # Back to main menu
    if parts[0] == "back":
        await start_command(update, context)
        return
    
    # Game mode selection
    if parts[0] == "mode":
        game_mode = parts[1]  # 'cpu' or 'duel'
        
        # Initialize match in cache
        matches_cache[chat_id] = {
            "match_id": f"{chat_id}_{int(datetime.now().timestamp())}",
            "players": [str(user.id)],
            "player_names": {str(user.id): user.first_name},
            "mode": game_mode,
            "state": "setup",
            "score": 0,
            "wickets": 0,
            "overs": 0,
            "balls": 0,
            "total_overs": 1,
            "max_wickets": 2,
            "created_at": datetime.now().isoformat()
        }
        
        if game_mode == "cpu":
            matches_cache[chat_id]["players"].append("cpu")
            matches_cache[chat_id]["player_names"]["cpu"] = "CPU Opponent"
            matches_cache[chat_id]["state"] = "toss"
            
            # Start toss for CPU mode
            await query.edit_message_text(
                f"{DIVIDER}\n🪙 **TOSS TIME**\n{DIVIDER}\n\n"
                f"{user.first_name}, call Heads or Tails:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("HEADS", callback_data=f"toss_heads_{chat_id}"),
                     InlineKeyboardButton("TAILS", callback_data=f"toss_tails_{chat_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Duel mode - wait for opponent
            await query.edit_message_text(
                f"{DIVIDER}\n👥 **WAITING FOR OPPONENT**\n{DIVIDER}\n\n"
                f"Share this with a friend to join!\n"
                f"Match ID: {matches_cache[chat_id]['match_id']}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ JOIN MATCH", callback_data=f"join_{chat_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    # Join match for duel mode
    if parts[0] == "join" and user.id not in [int(pid) for pid in matches_cache.get(chat_id, {}).get("players", []) if pid != "cpu"]:
        match = matches_cache.get(chat_id)
        if match and match["mode"] == "duel":
            match["players"].append(str(user.id))
            match["player_names"][str(user.id)] = user.first_name
            match["state"] = "toss"
            
            # Randomly select toss caller
            toss_caller = random.choice(match["players"])
            match["toss_caller"] = toss_caller
            
            await query.edit_message_text(
                f"{DIVIDER}\n🪙 **TOSS TIME**\n{DIVIDER}\n\n"
                f"{match['player_names'][toss_caller]}, call Heads or Tails:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("HEADS", callback_data=f"toss_heads_{chat_id}"),
                     InlineKeyboardButton("TAILS", callback_data=f"toss_tails_{chat_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    # Toss logic
    if parts[0] == "toss" and chat_id in matches_cache:
        match = matches_cache[chat_id]
        
        if match["state"] == "toss":
            toss_result = random.choice(["heads", "tails"])
            called = parts[1]  # heads or tails
            
            # Determine toss winner
            if match["mode"] == "cpu":
                # Player always wins toss in CPU mode for better experience
                toss_winner = str(user.id)
            else:
                # For duel, check if caller won
                if called == toss_result:
                    toss_winner = match["toss_caller"]
                else:
                    toss_winner = [p for p in match["players"] if p != match["toss_caller"]][0]
            
            match["toss_winner"] = toss_winner
            match["state"] = "batting_choice"
            
            await query.edit_message_text(
                f"🎉 {match['player_names'][toss_winner]} won the toss!\n\n"
                f"Choose your strategy:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏏 BAT FIRST", callback_data=f"bat_first_{chat_id}"),
                     InlineKeyboardButton("🎯 BOWL FIRST", callback_data=f"bowl_first_{chat_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    # Batting/Bowling choice
    if parts[0] in ["bat", "bowl"] and chat_id in matches_cache:
        match = matches_cache[chat_id]
        
        if match["state"] == "batting_choice":
            if parts[0] == "bat":
                match["batting"] = match["toss_winner"]
                match["bowling"] = [p for p in match["players"] if p != match["toss_winner"]][0]
            else:
                match["bowling"] = match["toss_winner"]
                match["batting"] = [p for p in match["players"] if p != match["toss_winner"]][0]
            
            match["state"] = "inning1"
            match["current_batsman"] = match["batting"]
            match["current_bowler"] = match["bowling"]
            
            await query.edit_message_text(
                f"✅ Match Setup Complete!\n\n"
                f"🏏 Batting: {match['player_names'][match['batting']]}\n"
                f"🎯 Bowling: {match['player_names'][match['bowling']]}\n\n"
                f"Let's play!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("1", callback_data=f"play_1_{chat_id}"),
                     InlineKeyboardButton("2", callback_data=f"play_2_{chat_id}"),
                     InlineKeyboardButton("3", callback_data=f"play_3_{chat_id}")],
                    [InlineKeyboardButton("4", callback_data=f"play_4_{chat_id}"),
                     InlineKeyboardButton("5", callback_data=f"play_5_{chat_id}"),
                     InlineKeyboardButton("6", callback_data=f"play_6_{chat_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )
        return
    
    # Gameplay - number selection
    if parts[0] == "play" and chat_id in matches_cache:
        match = matches_cache[chat_id]
        
        if match["state"] in ["inning1", "inning2"]:
            player_choice = int(parts[1])
            
            # Get opponent choice
            if match["current_bowler"] == "cpu":
                opponent_choice = random.randint(1, 6)
            else:
                # For duel mode, we need to wait for opponent's choice
                # Simplified version - auto-generate for now
                opponent_choice = random.randint(1, 6)
            
            # Process the ball
            match["balls"] += 1
            if match["balls"] == 6:
                match["overs"] += 1
                match["balls"] = 0
            
            if player_choice == opponent_choice:
                # Wicket
                match["wickets"] += 1
                result_text = f"🎯 WICKET! ({player_choice} vs {opponent_choice})"
            else:
                # Runs
                match["score"] += player_choice
                result_text = f"✨ {player_choice} runs! ({player_choice} vs {opponent_choice})"
            
            # Check if innings is over
            innings_over = (match["wickets"] >= match["max_wickets"] or 
                          match["overs"] >= match["total_overs"])
            
            if match["state"] == "inning1" and innings_over:
                # First innings over, set target and switch
                match["target"] = match["score"] + 1
                match["state"] = "inning2"
                match["score"] = 0
                match["wickets"] = 0
                match["overs"] = 0
                match["balls"] = 0
                
                # Swap batting and bowling
                match["current_batsman"], match["current_bowler"] = match["current_bowler"], match["current_batsman"]
                
                await query.edit_message_text(
                    f"🏁 FIRST INNINGS OVER!\n\n"
                    f"Target: {match['target']} runs\n\n"
                    f"Second innings starting now!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("1", callback_data=f"play_1_{chat_id}"),
                         InlineKeyboardButton("2", callback_data=f"play_2_{chat_id}"),
                         InlineKeyboardButton("3", callback_data=f"play_3_{chat_id}")],
                        [InlineKeyboardButton("4", callback_data=f"play_4_{chat_id}"),
                         InlineKeyboardButton("5", callback_data=f"play_5_{chat_id}"),
                         InlineKeyboardButton("6", callback_data=f"play_6_{chat_id}")]
                    ]),
                    parse_mode=ParseMode.MARKDOWN
                )
            elif match["state"] == "inning2" and (innings_over or match["score"] >= match["target"]):
                # Match over
                if match["score"] >= match["target"]:
                    winner = match["current_batsman"]
                    result = "CHASED SUCCESSFULLY!"
                else:
                    winner = match["current_bowler"]
                    result = "DEFENDED SUCCESSFULLY!"
                
                # Save match to MongoDB
                match_data = {
                    "match_id": match["match_id"],
                    "players": match["players"],
                    "player_names": match["player_names"],
                    "mode": match["mode"],
                    "winner": winner,
                    "winner_name": match["player_names"].get(winner, "CPU"),
                    "score": match["score"],
                    "wickets": match["wickets"],
                    "overs": match["overs"],
                    "balls": match["balls"],
                    "target": match.get("target", 0),
                    "result": result,
                    "completed_at": datetime.now().isoformat()
                }
                
                await save_match_to_db(match_data)
                
                # Update player stats
                if winner != "cpu":
                    await update_user_stats(int(winner), won=True, runs=match["score"])
                
                await query.edit_message_text(
                    f"🏆 **MATCH OVER!**\n\n"
                    f"👑 Winner: {match['player_names'].get(winner, 'CPU')}\n"
                    f"📊 Score: {match['score']}/{match['wickets']}\n"
                    f"📝 Result: {result}\n\n"
                    f"Thanks for playing!",
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Remove from cache
                matches_cache.pop(chat_id, None)
            else:
                # Continue playing
                scorecard = f"""
📊 **SCORECARD**
{result_text}

🏏 Batting: {match['player_names'][match['current_batsman']]}
🎯 Bowling: {match['player_names'][match['current_bowler']]}
📈 Score: {match['score']}/{match['wickets']}
⏰ Overs: {match['overs']}.{match['balls']}
                """
                
                if match["state"] == "inning2":
                    scorecard += f"\n🎯 Target: {match['target']}\n🚩 Need: {match['target'] - match['score']} runs"
                
                await query.edit_message_text(
                    scorecard,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("1", callback_data=f"play_1_{chat_id}"),
                         InlineKeyboardButton("2", callback_data=f"play_2_{chat_id}"),
                         InlineKeyboardButton("3", callback_data=f"play_3_{chat_id}")],
                        [InlineKeyboardButton("4", callback_data=f"play_4_{chat_id}"),
                         InlineKeyboardButton("5", callback_data=f"play_5_{chat_id}"),
                         InlineKeyboardButton("6", callback_data=f"play_6_{chat_id}")]
                    ]),
                    parse_mode=ParseMode.MARKDOWN
                )
        return

# ================= MAIN BOT SETUP =================
def main():
    """Main function to start the bot"""
    # Test MongoDB connection
    try:
        # Async connection test
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(client.server_info())
        print("✅ Connected to MongoDB")
    except Exception as e:
        print(f"⚠️  MongoDB connection issue: {e}")
        print("⚠️  Bot will run without database features")
    
    # Create bot application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cricket", start_cricket))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("✅ BOT IS ONLINE")
    
    # Start polling
    app.run_polling()

if __name__ == "__main__":
    main()
