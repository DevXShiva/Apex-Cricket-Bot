import os
import json
import random
import logging
from motor.motor_asyncio import AsyncIOMotorClient # MongoDB के लिए
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ================= CONFIGURATION =================
BOT3_TOKEN = os.getenv("BOT3_TOKEN", "YOUR_BOT3_TOKEN_HERE")
MONGO_URI = os.getenv("MONGO_URI", "your_mongodb_connection_string")

# Database और Collection के नाम (ताकि दूसरे डेटा से अलग रहे)
DB_NAME = "ApexCricketBot_DB" 
COLLECTION_NAME = "user_stats"

DIVIDER = "◈───────────────────◈"
FOOTER = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

matches_cache = {}

# ================= MONGODB SETUP =================
client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
stats_col = db[COLLECTION_NAME]

async def save_stats(uid, name):
    uid = str(uid)
    # upsert=True का मतलब है: अगर यूजर नहीं है तो बनाओ, है तो अपडेट करो
    await stats_col.update_one(
        {"_id": uid}, 
        {"$set": {"name": name}, "$inc": {"wins": 1}}, 
        upsert=True
    )

async def get_top_players():
    # टॉप 10 प्लेयर्स को सॉर्ट करके लाना
    cursor = stats_col.find().sort("wins", -1).limit(10)
    return await cursor.to_list(length=10)

# ================= ENGINE START =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro = (f"{DIVIDER}\n        🏏 **APEX CRICKET WORLD**\n{DIVIDER}\n\n"
             f"Welcome! Hand-Cricket on Telegram.\n\n"
             f"🏆 **Rules:** 1 Over Match | 2 Wickets Max.")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 VS CPU", callback_data=f"mode_cpu_{update.effective_chat.id}"),
         InlineKeyboardButton("👥 VS FRIEND", callback_data=f"mode_duel_{update.effective_chat.id}")],
        [InlineKeyboardButton("🏆 LEADERBOARD", callback_data=f"show_{update.effective_chat.id}")]
    ])
    
    await update.message.reply_text(intro + FOOTER, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid, data = str(user.id), query.data.split('_')
    action, chat_id = data[0], data[-1]

    # --- Leaderboard Logic (Now using MongoDB) ---
    if action == "show":
        sorted_stats = await get_top_players()
        lb_text = f"{DIVIDER}\n🏆 **TOP 10 PLAYERS**\n{DIVIDER}\n\n"
        
        if not sorted_stats:
            lb_text += "No records yet. Play a match!"
        for i, player in enumerate(sorted_stats, 1):
            lb_text += f"{i}. {player['name']} — {player['wins']} Wins\n"
        
        await query.edit_message_text(lb_text + FOOTER, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data=f"back_{chat_id}")]]), 
            parse_mode=ParseMode.MARKDOWN)
        return

    if action == "back":
        # Start command call करने के बजाय direct menu दिखाना बेहतर है
        await start_command(update, context)
        return

    # [ बाकी का गेमप्ले लॉजिक वैसा ही रहेगा जैसा आपने दिया था ]
    # बस end_match में save_stats को await करना होगा
    
    # ... (Keep your existing mode selection and toss logic here) ...
    # [ गेमप्ले और टॉस लॉजिक को यहाँ पेस्ट करें ]

# ================= CORE LOGIC (Updated for MongoDB) =================

async def end_match(query, m, cid, winner, reason):
    # Stats update in MongoDB
    if winner != "cpu":
        await save_stats(winner, m["names"][winner])
        
    status = (f"🏆 **MATCH OVER**\n👑 **WINNER:** {m['names'][winner]}\n📝 {reason}")
    await query.edit_message_text(status + FOOTER, parse_mode=ParseMode.MARKDOWN)
    matches_cache.pop(str(cid), None)

# ... [ get_num_kb और resolve_ball फंक्शन को भी रखें ] ...

def main():
    app = ApplicationBuilder().token(BOT3_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cricket", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("✅ PRO BOT ONLINE WITH MONGODB")
    app.run_polling()

if __name__ == "__main__":
    main()
