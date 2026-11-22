import asyncio
import os
from telethon import TelegramClient, events, Button
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.types import ChatBannedRights

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
BOT_TOKEN = "9121231358:AAE0Ziik2I28pKherq9Wo6tCSLC6Rf9Puf8"
GROUP_ID = -1003943756594
INTRO_TOPIC_ID = 9
GENERAL_CHAT_TOPIC_ID = 3
GENERAL_CHAT_LINK = "https://t.me/c/3943756594/3"
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teens_gc_banner.jpg")

# ==============================================================
# QUEUE SYSTEM STATE
# ==============================================================
verification_queue = []   # List of user entities waiting in queue
active_user = None        # User entity currently being served
active_msg_ids = []       # Message IDs belonging to the active verification session
active_timer_task = None  # Asyncio task for the 60s AFK timeout
user_data_cache = {}      # Temporary store for age/gender answers

bot = TelegramClient(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "gatekeeper_bot"),
    API_ID,
    API_HASH
)

async def restrict_user(user_id):
    """Restricts normal chat permissions until introduction is complete."""
    try:
        await bot(EditBannedRequest(
            channel=GROUP_ID,
            participant=user_id,
            banned_rights=ChatBannedRights(
                until_date=None,
                send_messages=True,
                send_media=True,
                send_stickers=True,
                send_gifs=True,
                send_games=True,
                send_inline=True,
                embed_links=True
            )
        ))
        print(f"[+] User {user_id} restricted.")
    except Exception as e:
        print(f"[!] Restrict error for {user_id}: {e}")

async def unrestrict_user(user_id):
    """Restores full messaging permissions."""
    try:
        await bot(EditBannedRequest(
            channel=GROUP_ID,
            participant=user_id,
            banned_rights=ChatBannedRights(
                until_date=None,
                send_messages=False,
                send_media=False,
                send_stickers=False,
                send_gifs=False,
                send_games=False,
                send_inline=False,
                embed_links=False
            )
        ))
        print(f"[+] User {user_id} unlocked.")
    except Exception as e:
        print(f"[!] Unrestrict error for {user_id}: {e}")

async def cleanup_active_messages():
    """Deletes all messages created during the active verification turn."""
    global active_msg_ids
    for mid in active_msg_ids:
        try:
            await bot.delete_messages(GROUP_ID, mid)
        except Exception:
            pass
    active_msg_ids = []

async def afk_timeout_worker(user_id, name):
    """Waits 60 seconds. If user doesn't complete, frees the turn for next person."""
    try:
        await asyncio.sleep(60)
        # If this user is still active after 60s, they went AFK!
        global active_user
        if active_user and active_user.id == user_id:
            print(f"[!] User {name} ({user_id}) went AFK (60s timeout).")
            await cleanup_active_messages()

            notice = await bot.send_message(
                GROUP_ID,
                f"⏳ [{name}](tg://user?id={user_id}), your turn timed out due to inactivity.\n"
                f"Whenever you are back, simply type **/verify** to take your turn!",
                reply_to=INTRO_TOPIC_ID
            )

            # Advance queue
            active_user = None
            asyncio.create_task(process_next_in_queue())

            # Delete timeout notice after 6 seconds
            await asyncio.sleep(6)
            try:
                await notice.delete()
            except Exception:
                pass
    except asyncio.CancelledError:
        pass

async def process_next_in_queue():
    """Picks the next user from the queue and presents their verification."""
    global active_user, active_msg_ids, active_timer_task, verification_queue

    if not verification_queue:
        active_user = None
        return

    # Pop next user
    active_user = verification_queue.pop(0)
    user = active_user
    name = user.first_name or "Friend"
    print(f"[-->] Serving user from queue: {name} (ID: {user.id})")

    active_msg_ids = []
    user_data_cache[user.id] = {}

    caption = (
        "╭──────────── 💗 ────────────╮\n"
        "   ✦ 𝐖 𝐄 𝐋 𝐂 𝐎 𝐌 𝐄   𝐓 𝐎   𝐓 𝐄 𝐄 𝐍 𝐒   𝐆 𝐂 ✦\n"
        "╰──────────── 💗 ────────────╯\n\n"
        f"👋 Hello [{name}](tg://user?id={user.id})! Welcome to Teens GC! 🫶\n"
        "A friendly & aesthetic community built for teens to chill, share memes, and make real friends.\n\n"
        "✨ Please complete your quick profile verification below to unlock chat access.\n\n"
        "⏳ ⚠️ **Notice:** You have **60 seconds** to verify before your turn passes to the next person!"
    )

    buttons = [
        [Button.inline("🛡️ Click Here to Verify", data=f"start_verify:{user.id}")]
    ]

    if os.path.exists(BANNER_PATH):
        msg = await bot.send_file(
            GROUP_ID,
            file=BANNER_PATH,
            caption=caption,
            buttons=buttons,
            reply_to=INTRO_TOPIC_ID
        )
    else:
        msg = await bot.send_message(
            GROUP_ID,
            caption,
            buttons=buttons,
            reply_to=INTRO_TOPIC_ID
        )

    active_msg_ids.append(msg.id)

    # Start 60-second AFK countdown
    if active_timer_task and not active_timer_task.done():
        active_timer_task.cancel()
    active_timer_task = asyncio.create_task(afk_timeout_worker(user.id, name))

def enqueue_user(user):
    """Enqueues a user and starts processing if bot is idle."""
    global active_user, verification_queue

    # If already in queue or currently active, skip duplicate
    if any(u.id == user.id for u in verification_queue) or (active_user and active_user.id == user.id):
        return

    verification_queue.append(user)
    print(f"[+] Enqueued {user.first_name} (Queue length: {len(verification_queue)})")

    if active_user is None:
        asyncio.create_task(process_next_in_queue())

# ==============================================================
# 1. NEW MEMBER ARRIVAL
# ==============================================================
@bot.on(events.ChatAction)
async def handle_new_member(event):
    if event.chat_id != GROUP_ID:
        return

    if event.user_joined or event.user_added:
        users = await event.get_users()
        for u in users:
            if u.bot:
                continue
            await restrict_user(u.id)
            enqueue_user(u)

# ==============================================================
# 2. TOPIC CLEANER & MANUAL /VERIFY TRIGGER (FOR AFK RETURN)
# ==============================================================
@bot.on(events.NewMessage)
async def introductions_cleaner(event):
    if event.chat_id != GROUP_ID or not event.message.reply_to:
        return

    reply_header = event.message.reply_to
    topic_id = reply_header.reply_to_top_id or reply_header.reply_to_msg_id

    if topic_id == INTRO_TOPIC_ID:
        sender = await event.get_sender()
        if not sender or sender.bot:
            return

        # If user (or admin testing) types /verify
        if event.raw_text.strip().lower() in ["/verify", "/intro", "/start"]:
            try:
                await event.delete()
            except Exception:
                pass
            enqueue_user(sender)
            return

        try:
            perms = await bot.get_permissions(GROUP_ID, sender.id)
            if perms.is_admin:
                return
        except Exception:
            pass

        # Delete any random chatting in Introductions immediately
        try:
            await event.delete()
            name = sender.first_name or "Member"
            notice = await bot.send_message(
                GROUP_ID,
                f"⚠️ [{name}](tg://user?id={sender.id}), this section is strictly for introductions. Please use 💬 **General Chat**.",
                reply_to=INTRO_TOPIC_ID
            )
            await asyncio.sleep(3)
            await notice.delete()
        except Exception:
            pass

# ==============================================================
# 3. INTERACTIVE CALLBACK HANDLERS
# ==============================================================
@bot.on(events.CallbackQuery)
async def handle_callback(event):
    global active_user, active_msg_ids, active_timer_task
    data = event.data.decode("utf-8")
    sender = await event.get_sender()
    sender_id = sender.id

    # --- STEP A: CLICKED "CLICK HERE TO VERIFY" ---
    if data.startswith("start_verify:"):
        _, target_id_str = data.split(":")
        target_id = int(target_id_str)

        if sender_id != target_id:
            await event.answer("⚠️ This is not your turn! Please wait for your card.", alert=True)
            return

        # Delete the welcome banner message
        try:
            await event.delete()
            if event.message_id in active_msg_ids:
                active_msg_ids.remove(event.message_id)
        except Exception:
            pass

        name = sender.first_name or "Friend"
        text = (
            f"👤 **Verification For: [{name}](tg://user?id={sender_id})**\n\n"
            f"╭──「 📌 𝐒𝐓𝐄𝐏 𝟏 𝐎𝐅 𝟐 」\n"
            f"┊ Please select your age bracket:\n"
            f"╰──────────────────────────\n"
            f"⏳ 60s timeout active"
        )
        buttons = [
            [
                Button.inline("12 - 14", data=f"age:{sender_id}:12-14"),
                Button.inline("15 - 18", data=f"age:{sender_id}:15-18")
            ],
            [
                Button.inline("18 - 25", data=f"age:{sender_id}:18-25"),
                Button.inline("25 - 30", data=f"age:{sender_id}:25-30")
            ]
        ]
        msg = await bot.send_message(GROUP_ID, text, buttons=buttons, reply_to=INTRO_TOPIC_ID)
        active_msg_ids.append(msg.id)
        await event.answer()

    # --- STEP B: AGE SELECTION ---
    elif data.startswith("age:"):
        _, target_id_str, age_val = data.split(":")
        target_id = int(target_id_str)

        if sender_id != target_id:
            await event.answer("⚠️ This is not your turn! Please wait for your card.", alert=True)
            return

        if sender_id not in user_data_cache:
            user_data_cache[sender_id] = {}
        user_data_cache[sender_id]["age"] = age_val

        name = sender.first_name or "Friend"
        text = (
            f"👤 **Verification For: [{name}](tg://user?id={sender_id})**\n\n"
            f"✅ **Age Bracket:** `{age_val}`\n\n"
            f"╭──「 📌 𝐒𝐓𝐄𝐏 𝟐 𝐎𝐅 𝟐 」\n"
            f"┊ Please select your gender:\n"
            f"╰──────────────────────────"
        )
        buttons = [
            [
                Button.inline("👦 Male", data=f"gen:{sender_id}:Male"),
                Button.inline("👧 Female", data=f"gen:{sender_id}:Female")
            ]
        ]
        await event.edit(text, buttons=buttons)
        await event.answer()

    # --- STEP C: GENDER SELECTION & FINAL RULES CARD ---
    elif data.startswith("gen:"):
        _, target_id_str, gen_val = data.split(":")
        target_id = int(target_id_str)

        if sender_id != target_id:
            await event.answer("⚠️ This is not your turn! Please wait for your card.", alert=True)
            return

        # Cancel AFK timer since user completed
        if active_timer_task and not active_timer_task.done():
            active_timer_task.cancel()

        age_val = user_data_cache.get(sender_id, {}).get("age", "15 - 18")
        name = sender.first_name or "Friend"

        # Delete question message
        try:
            await event.delete()
            if event.message_id in active_msg_ids:
                active_msg_ids.remove(event.message_id)
        except Exception:
            pass

        final_rules_card = (
            "╭──────────── 💗 ────────────╮\n"
            "   ✦ 𝐘 𝐎 𝐔   𝐀 𝐑 𝐄   𝐕 𝐄 𝐑 Ｉ Ｆ Ｉ 𝐄 𝐃 ✦\n"
            "╰──────────── 💗 ────────────╯\n\n"
            f"🎉 Welcome to the family, [{name}](tg://user?id={sender_id})! 🫶\n"
            "You have been successfully verified and chat access is unlocked.\n\n"
            "╭──「 👤 𝐕𝐄𝐑𝐈𝐅𝐈𝐄𝐃 𝐏𝐑𝐎𝐅Ｉ𝐋𝐄 」\n"
            f"┊ ✦ Name     ➔  {name}\n"
            f"┊ ✦ Age      ➔  {age_val}\n"
            f"┊ ✦ Gender   ➔  {gen_val}\n"
            "┊ ✦ Status   ➔  Active Member ✅\n"
            "╰──────────────────────────\n\n"
            "╭──「 📜 𝐆𝐑𝐎𝐔𝐏 𝐑𝐔𝐋𝐄𝐒 」\n"
            "┊ 🚫 𝐍𝐨 𝐓𝐨𝐱𝐢𝐜𝐢𝐭𝐲 ➔ No bad words or abusive behavior (Aegis Active)\n"
            "┊ 🔞 𝐍𝐨 𝟏𝟖+ / 𝐍𝐒𝐅𝐖 ➔ Strictly no adult media or sexual content\n"
            "┊ 🔗 𝐍𝐨 𝐒𝐩𝐚𝐦 / 𝐋𝐢𝐧𝐤𝐬 ➔ No promotional links or unsolicited DMs\n"
            "┊ 🤍 𝐑𝐞𝐬𝐩𝐞𝐜𝐭 𝐀𝐥𝐥 ➔ Treat everyone with kindness and respect\n"
            "┊ 🎵 𝐌𝐮𝐬𝐢𝐜 𝐒𝐡𝐚𝐫𝐢𝐧𝐠 ➔ Music links allowed in 🎵 Songs topic only\n"
            "╰──────────────────────────\n\n"
            "╭──────────── 💕 ────────────╮\n"
            "   ✦ 𝐄𝐧𝐣𝐨𝐲 𝐲𝐨𝐮𝐫 𝐬𝐭𝐚𝐲 & 𝐤𝐞𝐞𝐩 𝐬𝐦𝐢𝐥𝐢𝐧𝐠 ✦\n"
            "╰──────────── 💕 ────────────╯"
        )

        buttons = [
            [Button.url("💬 Jump to General Chat", GENERAL_CHAT_LINK)]
        ]

        intro_card = await bot.send_message(
            GROUP_ID,
            final_rules_card,
            buttons=buttons,
            reply_to=INTRO_TOPIC_ID
        )
        active_msg_ids.append(intro_card.id)

        # Restore all messaging permissions
        await unrestrict_user(sender_id)
        user_data_cache.pop(sender_id, None)
        await event.answer("🎉 Verification Complete! You can now chat in the group.", alert=True)

        # Post welcoming notification directly into General Chat (Topic 3) so everyone sees them switch!
        try:
            await bot.send_message(
                GROUP_ID,
                f"🎉 **Welcome [{sender.first_name}](tg://user?id={sender_id}) to 💬 General Chat!** 🫶\n"
                f"✨ Verification complete! Everyone give them a warm welcome! 👋",
                reply_to=GENERAL_CHAT_TOPIC_ID
            )
        except Exception as e:
            print(f"[!] Error welcoming user to General Chat: {e}")

        # Automatically advance queue and delete card after 15 seconds to keep Topic clean
        async def auto_finish(card):
            global active_user
            await asyncio.sleep(15)
            try:
                await card.delete()
                if card.id in active_msg_ids:
                    active_msg_ids.remove(card.id)
            except Exception:
                pass
            active_user = None
            await process_next_in_queue()

        asyncio.create_task(auto_finish(intro_card))

async def main():
    print("=" * 60)
    print("      GATEKEEPER BOT - SMART QUEUE ENGINE ACTIVE       ")
    print("=" * 60)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"[+] Bot Online: @{me.username} ({me.first_name})")
    print(f"[+] Guarding Group: {GROUP_ID}")
    print("[+] 1-by-1 Queue Turnstile & 60s AFK Protection ACTIVE!\n")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.")
