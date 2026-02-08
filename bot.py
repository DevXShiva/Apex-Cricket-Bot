import os
import json
import random
import time
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ================= CONFIGURATION =================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" 
ADMIN_ID = 5298223577

PLAYERS_FILE = "players.json"
players_cache = {"players": {}}
matches_cache = {}

# FOOTER FOR EVERY MESSAGE
FOOTER = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"

# ================= UI ELEMENTS =================
DIVIDER = "◈───────────────────◈"

# ================= COMMENTARY =================
COMMENTARY = {
    "runs": {
        1: ["Nudged for a quick single.", "Excellent running!"],
        2: ["Two runs! Great hustle between wickets."],
        3: ["Brilliant! They've taken three!"],
    },
    "fours": ["🔥 FOUR! Pierced the gap perfectly!"],
    "sixes": ["🚀 SIX! Massive hit over the ropes!"],
    "wickets": ["🎯 OUT! A huge blow for the team!"],
}

# ================= DATA HELPERS =================
def load_data():
    if os.path.exists(PLAYERS_FILE):
        with open(PLAYERS_FILE, 'r') as f:
            data = json.load(f)
            players_cache["players"] = data.get("players", {})

def save_data():
    with open(PLAYERS_FILE, 'w') as f:
        json.dump({"players": players_cache["players"]}, f, indent=2)

load_data()

def update_stats(user_id, name, win, score):
    uid = str(user_id)
    if uid not in players_cache["players"]:
        players_cache["players"][uid] = {"name": name, "m": 0, "w": 0, "l": 0, "hs": 0}
    p = players_cache["players"][uid]
    p["m"] += 1
    if win: p["w"] += 1
    else: p["l"] += 1
    if score > p["hs"]: p["hs"] = score
    save_data()

# ================= INTRO & RULES =================
def get_intro_text():
    return (
        f"{DIVIDER}\n"
        f"       🏏 **APEX CRICKET ARENA**\n"
        f"{DIVIDER}\n\n"
        f"**Welcome to the ultimate Cricket Bot!**\n\n"
        f"📜 **BASIC RULES:**\n"
        f"• Pick a number from 1 to 6.\n"
        f"• If Batsman & Bowler pick different numbers, Batsman gets the runs.\n"
        f"• If numbers match, it's a **WICKET**!\n\n"
        f"🕹 **HOW TO PLAY:**\n"
        f"1. Choose VS CPU or VS Friend.\n"
        f"2. Win the Toss & choose Bat/Bowl.\n"
        f"3. Reach the target to win!\n\n"
        f"Use /stats to see your rank."
    )

# ================= COMMANDS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_intro_text() + FOOTER, 
        reply_markup=get_main_menu_kb(str(update.effective_chat.id)), 
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def start_cricket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if cid in matches_cache:
        await update.message.reply_text("⚠️ A match is already live in this chat!" + FOOTER, parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(get_intro_text() + FOOTER, 
        reply_markup=get_main_menu_kb(cid), 
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    p = players_cache["players"].get(uid)
    if not p:
        await update.message.reply_text("❌ No records found! Play a match first." + FOOTER, parse_mode=ParseMode.MARKDOWN)
        return
    text = (f"{DIVIDER}\n"
            f"       📊 **PLAYER STATS**\n"
            f"{DIVIDER}\n\n"
            f"👤 **Name:** {p['name']}\n"
            f"🏟 **Matches:** {p['m']}\n"
            f"🏆 **Wins:** {p['w']}\n"
            f"📉 **Losses:** {p['l']}\n"
            f"🚀 **Best Score:** {p['hs']}") + FOOTER
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# ================= CALLBACKS =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid = str(user.id)
    data = query.data.split('_')
    action, chat_id = data[0], data[-1]

    if chat_id not in matches_cache and action != "mode" and action != "j":
        # Initializing if new match
        if action == "mode" or action == "j": pass
        else:
            await query.answer("Session expired!", show_alert=True)
            return

    if action == "mode":
        matches_cache[chat_id] = {"creator": uid, "state": "init", "last_act": time.time(), "players": [], "names": {}}
        m = matches_cache[chat_id]
        is_cpu = (data[1] == "cpu")
        m.update({"cpu_mode": is_cpu, "players": [uid, "cpu"] if is_cpu else [], 
                  "names": {uid: user.first_name, "cpu": "APEX CPU"},
                  "score": 0, "wickets": 0, "overs": 0, "balls": 0, "choices": {},
                  "total_overs": 1, "max_wickets": 2})
        
        if is_cpu:
            m["toss_caller"] = uid
            await query.edit_message_text(f"{DIVIDER}\n      🤖 **CPU MODE**\n{DIVIDER}\n\n{user.first_name}, call the toss:" + FOOTER, 
                reply_markup=get_toss_kb(chat_id), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await query.edit_message_text(f"{DIVIDER}\n      👥 **DUEL MODE**\n{DIVIDER}\n\nWaiting for an opponent..." + FOOTER, 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ JOIN MATCH", callback_data=f"j_{chat_id}")]])
            , parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    m = matches_cache.get(chat_id)
    if not m: return

    if action == "j":
        if uid in m["players"]: return
        m["players"].append(uid)
        m["names"][uid] = user.first_name
        m["toss_caller"] = random.choice(m["players"])
        await query.edit_message_text(f"{DIVIDER}\n      🪙 **THE TOSS**\n{DIVIDER}\n\n{m['names'][m['toss_caller']]}, it's your call:" + FOOTER, 
            reply_markup=get_toss_kb(chat_id), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    if uid not in m.get("players", []):
        await query.answer("👀 Spectator Mode: You can only watch!", show_alert=True)
        return

    if action in ["th", "tt"]:
        if uid != m["toss_caller"]: return
        win = (random.choice([0,1])==1) or m["cpu_mode"]
        m["toss_winner"] = uid if win else [p for p in m["players"] if p != uid][0]
        await query.edit_message_text(f"{DIVIDER}\n      🎊 **TOSS WON**\n{DIVIDER}\n\n{m['names'][m['toss_winner']]} wins! Choose your side:" + FOOTER, 
            reply_markup=get_choice_kb(chat_id), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    if action in ["tb", "tw"]:
        if uid != m["toss_winner"]: return
        p1, p2 = m["players"]
        if action == "tb": m["bat_f"], m["bowl_f"] = uid, (p2 if uid==p1 else p1)
        else: m["bowl_f"], m["bat_f"] = uid, (p2 if uid==p1 else p1)
        m.update({"current_batsman": m["bat_f"], "current_bowler": m["bowl_f"], "state": "inning1"})
        await query.edit_message_text(f"{DIVIDER}\n      🏏 **MATCH START**\n{DIVIDER}\n\nBatsman: {m['names'][m['current_batsman']]}\nBowler: {m['names'][m['current_bowler']]}" + FOOTER, 
            reply_markup=get_num_kb(chat_id), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    if action.startswith('n'):
        if uid not in [m["current_batsman"], m["current_bowler"]]: return
        if uid in m["choices"]: return
        m["choices"][uid] = int(action[1])
        if m["cpu_mode"]: m["choices"]["cpu"] = random.randint(1,6)
        if len(m["choices"]) == 2: await resolve_ball(query, m, chat_id)
        return

# ================= LIVE ENGINE =================
async def resolve_ball(query, m, cid):
    bat_id, bowl_id = m["current_batsman"], m["current_bowler"]
    b1, b2 = m["choices"][bat_id], m["choices"][bowl_id]
    m["choices"] = {}
    m["balls"] += 1
    if m["balls"] == 6: m["overs"] += 1; m["balls"] = 0
    
    event = f"🎯 **WICKET!**" if b1 == b2 else f"✨ **{b1} RUNS!**"
    if b1 == b2: m["wickets"] += 1
    else: m["score"] += b1

    if m["wickets"] >= m["max_wickets"] or m["overs"] >= m["total_overs"]:
        if m["state"] == "inning1":
            m.update({"target": m["score"]+1, "state": "inning2", "current_batsman": m["bowl_f"], 
                      "current_bowler": m["bat_f"], "score": 0, "wickets": 0, "overs": 0, "balls": 0})
            await query.edit_message_text(f"{DIVIDER}\n      🧢 **INNINGS BREAK**\n{DIVIDER}\n\nTarget: {m['target']}\nNext Batsman: {m['names'][m['current_batsman']]}" + FOOTER, 
                reply_markup=get_num_kb(cid), parse_mode=ParseMode.MARKDOWN)
        else:
            winner = bat_id if m["score"] >= m.get("target", 999) else bowl_id
            await end_match(query, m, cid, winner)
    elif m["state"] == "inning2" and m["score"] >= m["target"]:
        await end_match(query, m, cid, bat_id)
    else:
        status = (
            f"{DIVIDER}\n"
            f"      📊 **LIVE SCORECARD**\n"
            f"{DIVIDER}\n\n"
            f"🏏 {m['names'][bat_id]}: {b1}  ◈  🎯 {m['names'][bowl_id]}: {b2}\n"
            f"───\n"
            f"{event}\n"
            f"───\n"
            f"📈 Score: {m['score']}/{m['wickets']} ({m['overs']}.{m['balls']}/{m['total_overs']})\n"
        )
        if m["state"] == "inning2":
            status += f"🎯 Target: {m['target']} (Need {m['target']-m['score']} runs)"
        
        await query.edit_message_text(status + FOOTER, reply_markup=get_num_kb(cid), parse_mode=ParseMode.MARKDOWN)

async def end_match(query, m, cid, win_id):
    name = m["names"].get(win_id, "Winner")
    if win_id != "cpu": update_stats(win_id, name, True, m["score"])
    text = (f"{DIVIDER}\n"
            f"      🏆 **MATCH OVER**\n"
            f"{DIVIDER}\n\n"
            f"👑 **WINNER:** {name}\n"
            f"📊 **Final Score:** {m['score']}/{m['wickets']}\n\n"
            f"Type /cricket for a rematch!") + FOOTER
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    matches_cache.pop(str(cid), None)

# ================= KEYBOARDS =================
def get_main_menu_kb(cid):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🤖 VS CPU", callback_data=f"mode_cpu_{cid}"),
        InlineKeyboardButton("👥 VS FRIEND", callback_data=f"mode_duel_{cid}")
    ]])

def get_toss_kb(cid): return InlineKeyboardMarkup([[InlineKeyboardButton("⚪ HEADS", callback_data=f"th_{cid}"), InlineKeyboardButton("⚫ TAILS", callback_data=f"tt_{cid}")]])
def get_choice_kb(cid): return InlineKeyboardMarkup([[InlineKeyboardButton("🏏 BAT", callback_data=f"tb_{cid}"), InlineKeyboardButton("🎯 BOWL", callback_data=f"tw_{cid}")]])
def get_num_kb(cid):
    btns = [[InlineKeyboardButton(str(i), callback_data=f"n{i}_{cid}") for i in range(1,4)],
            [InlineKeyboardButton(str(i), callback_data=f"n{i}_{cid}") for i in range(4,7)],
            [InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{cid}")]]
    return InlineKeyboardMarkup(btns)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cricket", start_cricket))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("✅ BOT IS ONLINE")
    app.run_polling()

if __name__ == "__main__": main()
