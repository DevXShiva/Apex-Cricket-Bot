import os
import random
import asyncio
import uuid
from threading import Thread
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from motor.motor_asyncio import AsyncIOMotorClient

# ================= FLASK SERVER FOR UPTIME =================
app = Flask('')

@app.route('/')
def home():
    return "Apex Cricket Bot is Running 24/7! 🏏"

def run_web():
    app.run(host='0.0.0.0', port=8080)

# ================= DATABASE SETUP =================
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client["ApexCricket_DB"]
stats_col = db["UserStats"]
groups_col = db["GroupLogs"]

ADMIN_ID = int(os.getenv("ADMIN_ID", 5298223577))
DIVIDER = "✨━━━━━━━━━━━━━━━━━━✨"
STADIUM_EMOJI = "🏟️"
FOOTER = "\n\n───\n🌟 **Powered by [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

# Global In-Memory Cache for Matches
matches = {}

# ================= HELPERS =================

async def update_db_stats(uid, name, won=False, runs=0):
    user = await stats_col.find_one({"_id": str(uid)})
    if not user:
        user = {"_id": str(uid), "name": name, "wins": 0, "matches": 0, "hs": 0, "runs": 0}
    
    user["matches"] += 1
    if won: user["wins"] += 1
    if runs > user["hs"]: user["hs"] = runs
    user["runs"] += runs
    user["name"] = name
    await stats_col.replace_one({"_id": str(uid)}, user, upsert=True)

def get_game_kb(m_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣", callback_data=f"n_1_{m_id}"), InlineKeyboardButton("2️⃣", callback_data=f"n_2_{m_id}"), InlineKeyboardButton("3️⃣", callback_data=f"n_3_{m_id}")],
        [InlineKeyboardButton("4️⃣", callback_data=f"n_4_{m_id}"), InlineKeyboardButton("5️⃣", callback_data=f"n_5_{m_id}"), InlineKeyboardButton("6️⃣", callback_data=f"n_6_{m_id}")],
        [InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surr_{m_id}")]
    ])

async def auto_cancel(m_id, context):
    await asyncio.sleep(120)
    if m_id in matches and matches[m_id]['state'] == "waiting":
        chat_id = matches[m_id]['chat_id']
        del matches[m_id]
        try: await context.bot.send_message(chat_id, "⏰ Match timed out! No one joined.")
        except: pass

# ================= COMMANDS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m_id = str(uuid.uuid4())[:8].upper()
    uid = str(update.effective_user.id)
    
    if update.effective_chat.type != "private":
        link = await update.effective_chat.export_invite_link() if update.effective_chat.username else "Private Group"
        await groups_col.update_one({"_id": str(update.effective_chat.id)}, {"$set": {"title": update.effective_chat.title, "link": link}}, upsert=True)

    txt = f"{STADIUM_EMOJI} **APEX CRICKET ARENA**\n{DIVIDER}\nID: `{m_id}`\nChallenge your friend or play with AI!"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 VS CPU", callback_data=f"m_cpu_{m_id}"),
         InlineKeyboardButton("👥 VS FRIEND", callback_data=f"m_duel_{m_id}")]
    ])
    await update.message.reply_text(txt + FOOTER, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_ID and update.effective_chat.type == "private":
        u_count = await stats_col.count_documents({})
        g_count = await groups_col.count_documents({})
        msg = f"📊 **ADMIN STATS**\nTotal Users: {u_count}\nTotal Groups: {g_count}\n\n**Recent Groups:**\n"
        async for g in groups_col.find().limit(5):
            msg += f"• [{g['title']}]({g.get('link',' ')})\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    target_id = str(user.id)
    if context.args:
        arg = context.args[0].replace("@", "")
        target_id = arg if arg.isdigit() else target_id

    data = await stats_col.find_one({"_id": target_id})
    if not data: return await update.message.reply_text("❌ No stats found.")
    
    msg = (f"👤 **STATS: {data['name']}**\n{DIVIDER}\n"
           f"Matches: {data['matches']} | Wins: {data['wins']}\n"
           f"Highest: {data['hs']} | Total Runs: {data['runs']}")
    await update.message.reply_text(msg + FOOTER, parse_mode=ParseMode.MARKDOWN)

# ================= GAME ENGINE =================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = str(update.effective_user.id)
    data = query.data.split('_')
    action, val, m_id = data[0], data[1], data[2]

    if action == "m":
        matches[m_id] = {
            "chat_id": update.effective_chat.id, "state": "waiting",
            "players": [uid], "names": {uid: update.effective_user.first_name},
            "score": 0, "wickets": 0, "balls": 0, "history": [], "choices": {}, "inning": 1
        }
        if val == "cpu":
            matches[m_id].update({"players": [uid, "cpu"], "names": {uid: update.effective_user.first_name, "cpu": "🤖 CPU"}})
            await start_toss(query, m_id)
        else:
            asyncio.create_task(auto_cancel(m_id, context))
            await query.edit_message_text(f"🤝 **Match ID: `{m_id}`**\nWaiting for opponent to join...", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏏 JOIN", callback_data=f"j_x_{m_id}")]]))

    elif action == "j" and uid not in matches.get(m_id, {}).get("players", []):
        m = matches[m_id]
        m["players"].append(uid)
        m["names"][uid] = update.effective_user.first_name
        await start_toss(query, m_id)

    elif action == "t": # Toss
        m = matches[m_id]
        if uid != m["players"][0]: return
        winner = random.choice(m["players"])
        m["toss_win"] = winner
        await query.edit_message_text(f"🎊 {m['names'][winner]} won the toss!", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏏 BAT", callback_data=f"dec_bat_{m_id}"), InlineKeyboardButton("🎯 BOWL", callback_data=f"dec_bowl_{m_id}")]]))

    elif action == "dec": # Decision
        m = matches[m_id]
        if uid != m["toss_win"]: return
        if val == "bat": m["bat"], m["bowl"] = uid, [p for p in m["players"] if p != uid][0]
        else: m["bowl"], m["bat"] = uid, [p for p in m["players"] if p != uid][0]
        m["state"] = "playing"
        await update_board(query, m_id)

    elif action == "n": # Number Selection
        m = matches.get(m_id)
        if not m or uid not in [m["bat"], m["bowl"]] or uid in m["choices"]: return
        m["choices"][uid] = int(val)
        if "cpu" in m["players"]: m["choices"]["cpu"] = random.randint(1, 6)
        
        if len(m["choices"]) == 2:
            await resolve_ball(query, m_id)

async def start_toss(query, m_id):
    matches[m_id]["state"] = "toss"
    await query.edit_message_text("🪙 **TOSS TIME!**\nCaller: " + matches[m_id]["names"][matches[m_id]["players"][0]], 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌕 HEADS", callback_data=f"t_h_{m_id}"), InlineKeyboardButton("🌑 TAILS", callback_data=f"t_t_{m_id}")]]))

async def resolve_ball(query, m_id):
    m = matches[m_id]
    b_val, bo_val = m["choices"][m["bat"]], m["choices"][m["bowl"]]
    m["choices"] = {}
    m["balls"] += 1
    
    if b_val == bo_val: # OUT
        m["wickets"] += 1
        m["history"].append("🔴")
        res = f"☝️ **OUT! ({b_val} vs {bo_val})**"
    else:
        m["score"] += b_val
        m["history"].append(f"`{b_val}`")
        res = f"✨ **{b_val} RUNS!**"

    # End Inning or Match logic
    if m["inning"] == 1:
        if m["wickets"] >= 1 or m["balls"] >= 6:
            m["target"] = m["score"] + 1
            m["inning"] = 2
            m["bat"], m["bowl"] = m["bowl"], m["bat"]
            m["score"], m["wickets"], m["balls"], m["history"] = 0, 0, 0, []
            await query.edit_message_text(f"🏁 **Inning Over!**\nTarget: {m['target']}", reply_markup=get_game_kb(m_id))
        else: await update_board(query, m_id, res)
    else:
        if m["score"] >= m["target"]:
            await end_game(query, m_id, m["bat"], "Target Chased!")
        elif m["wickets"] >= 1 or m["balls"] >= 6:
            await end_game(query, m_id, m["bowl"], "Target Defended!")
        else: await update_board(query, m_id, res)

async def update_board(query, m_id, last="Game Started!"):
    m = matches[m_id]
    txt = (f"{STADIUM_EMOJI} **MATCH ID: `{m_id}`**\n{DIVIDER}\n"
           f"📢 {last}\n\n🏏 Bat: {m['names'][m['bat']]}\n🎯 Bowl: {m['names'][m['bowl']]}\n"
           f"📊 Score: {m['score']}/{m['wickets']} ({m['balls']}/6)\n"
           f"📝 History: {' '.join(m['history'])}\n")
    if m["inning"] == 2: txt += f"🚩 Target: {m['target']} (Need {m['target']-m['score']})"
    await query.edit_message_text(txt + FOOTER, reply_markup=get_game_kb(m_id), parse_mode=ParseMode.MARKDOWN)

async def end_game(query, m_id, winner, reason):
    m = matches[m_id]
    txt = f"🏆 **MATCH FINISHED**\n{DIVIDER}\nWinner: {m['names'][winner]}\nReason: {reason}\nFinal Score: {m['score']}/{m['wickets']}"
    await query.edit_message_text(txt + FOOTER, parse_mode=ParseMode.MARKDOWN)
    # Update Stats in DB
    for p_id in m["players"]:
        if p_id != "cpu":
            won = (p_id == winner)
            # Find runs for this player in either inning
            await update_db_stats(p_id, m["names"][p_id], won)
    del matches[m_id]

# ================= MAIN =================

# पुराने main() को इससे बदलें
async def main():
    # वेब सर्वर शुरू करें (Render के पोर्ट बाइंडिंग के लिए)
    Thread(target=run_web, daemon=True).start()

    token = os.getenv("BOT_TOKEN")
    # Application को सही तरीके से बिल्ड करें
    application = ApplicationBuilder().token(token).build()

    # Handlers जोड़ें
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cricket", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    print("✅ Bot is Online and Polling...")

    # पक्का करें कि पिछला कोई पुराना कनेक्शन न बचा हो
    async with application:
        await application.initialize()
        await application.start()
        # यहाँ drop_pending_updates=True डालने से पुराने अटके हुए मैसेज एरर नहीं देंगे
        await application.updater.start_polling(drop_pending_updates=True)
        
        # बॉट को चालू रखने के लिए
        while True:
            await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot Stopped.")
