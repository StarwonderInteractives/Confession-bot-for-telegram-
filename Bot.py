# confession_bot.py
# Requires: python-telegram-bot v20+
# pip install python-telegram-bot

import json
import time
import logging
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# -------- CONFIG --------
TOKEN = "8550613166:AAHinG2HeFMixOJA0fRzMy695D6qHT_78x0"  # replace with your BotFather token
DATA_FILE = Path("confess_data.json")
COOLDOWN_SECONDS = 30  # per-user DM cooldown
# ------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def load_data() -> dict:
    default = {"group_id": None, "enabled": True, "count": 0, "admins": []}
    if DATA_FILE.exists():
        try:
            with DATA_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # ensure keys present
            for k, v in default.items():
                data.setdefault(k, v)
            return data
        except Exception:
            logger.exception("Failed to load data file — starting fresh.")
            return default
    else:
        return default


def save_data(data: dict) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# in-memory cooldown map (user_id -> last_ts)
last_message_ts: dict[int, float] = {}
data = load_data()


# ---------- Helpers ----------
def is_admin(user_id: int) -> bool:
    return user_id in data.get("admins", [])


def add_admin(user_id: int):
    if user_id not in data["admins"]:
        data["admins"].append(user_id)
        save_data(data)


# ---------- Handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "yo! i'm the Confession Bot.\n\n"
        "DM me your confession and i'll post it anonymously to the group (if set).\n"
        "Commands:\n"
        "/confess - instructions\n"
        "/help - same as /confess\n\n"
        "Group admins: use /setgroup in the group to attach it.\n"
        "Admins can use /toggleconfessions to enable/disable posting."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)


async def confess_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just send me a private message containing the confession. "
        "I'll post it anonymously to the linked group."
    )


async def setgroup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only usable in groups
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if chat.type in ("group", "supergroup"):
        # check if user is group admin (let's try a basic check)
        # We can't always confirm without getChatMember, so attempt:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if not (member.status in ("administrator", "creator")):
                await msg.reply_text("Only group admins can set this group as the confession target.")
                return
        except Exception:
            # fallback: allow only bot owner (first admin in data) or previously added admins
            if not is_admin(user.id):
                await msg.reply_text("You must be a group admin to set the group (or be in bot admins).")
                return

        data["group_id"] = chat.id
        # Optionally add the setter as a bot-admin for convenience
        add_admin(user.id)
        save_data(data)
        await msg.reply_text(f"✅ This group is now set as the confession destination (id: {chat.id}).")
    else:
        await msg.reply_text("/setgroup must be used inside the target group by a group admin.")


async def toggleconfessions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    # permit group admins (if used in group) or saved bot admins
    chat = update.effective_chat
    allowed = False

    if chat.type in ("group", "supergroup"):
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status in ("administrator", "creator"):
                allowed = True
        except Exception:
            allowed = is_admin(user.id)
    else:
        allowed = is_admin(user.id)

    if not allowed:
        await msg.reply_text("You don't have permission to toggle confessions.")
        return

    data["enabled"] = not data.get("enabled", True)
    save_data(data)
    await msg.reply_text(f"Confessions enabled: {data['enabled']}")


async def incoming_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles private messages to the bot — treats text as a confession.
    """
    msg = update.effective_message
    user = update.effective_user

    # only accept private chats
    if update.effective_chat.type != "private":
        return

    # check if confessions are enabled and group set
    if not data.get("enabled", True):
        await msg.reply_text("Confessions are currently disabled. Try later.")
        return

    group_id: Optional[int] = data.get("group_id")
    if not group_id:
        await msg.reply_text("No group has been set yet. Ask an admin to use /setgroup in the target group.")
        return

    # cooldown
    last_ts = last_message_ts.get(user.id, 0)
    now = time.time()
    if now - last_ts < COOLDOWN_SECONDS:
        wait = int(COOLDOWN_SECONDS - (now - last_ts))
        await msg.reply_text(f"Slow down fam — wait {wait}s before sending another confession.")
        return

    text = msg.text or ""
    # optional: ignore empty or too long
    if not text.strip():
        await msg.reply_text("Can't send empty confessions — type something juicy.")
        return
    if len(text) > 1200:
        await msg.reply_text("Too long! Keep confessions under 1200 characters.")
        return

    # increment confession count, persist
    data["count"] = data.get("count", 0) + 1
    cnum = data["count"]
    save_data(data)

    # post to group
    post_text = f"#Confession{cnum}\n\n\"{text.strip()}\""
    try:
        await context.bot.send_message(chat_id=group_id, text=post_text)
        await msg.reply_text("Sent! Your confession was posted anonymously. ✨")
        last_message_ts[user.id] = now
    except Exception as e:
        logger.exception("Failed to send confession to group")
        await msg.reply_text("Failed to post confession — make sure the bot is in the target group and has permission to send messages.")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # public status
    gid = data.get("group_id")
    enabled = data.get("enabled", True)
    cnt = data.get("count", 0)
    await update.message.reply_text(
        f"Group set: {gid}\nConfessions enabled: {enabled}\nTotal confessions: {cnt}"
    )


# ---------- Main ----------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("confess", confess_cmd))
    app.add_handler(CommandHandler("setgroup", setgroup_cmd))
    app.add_handler(CommandHandler("toggleconfessions", toggleconfessions_cmd))
    app.add_handler(CommandHandler("status", status_cmd))

    # Private text messages go to confession handler
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, incoming_private_message))

    # Start
    logger.info("Starting Confession Bot...")
    app.run_polling(allowed_updates=["message", "edited_message", "callback_query"])


if __name__ == "__main__":
    main()