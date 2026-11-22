import asyncio
import os
import re
import json
import datetime
from telethon import TelegramClient, events
from telethon.tl.functions.channels import EditBannedRequest, GetParticipantsRequest
from telethon.tl.types import ChatBannedRights, ChannelParticipantsSearch, ChannelParticipantsBanned

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
BOT_TOKEN = "9076120795:AAF9RcMisWz6tFT8lLjF8AQfQLu8ez2sIrI"
GROUP_ID = -1003943756594
MUSIC_TOPIC_ID = 5  # Topic 5: 🎵 Songs & Music Vibes (Allows Spotify & YouTube only)
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aegis_records.json")

def load_records():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_records(records):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
    except Exception as e:
        print(f"[!] Error saving records: {e}")

# ==========================================================
# 1. MULTI-LANGUAGE PROFANITY & OBFUSCATION DETECTOR
# ==========================================================
LEET_TRANS = str.maketrans({
    "0": "o", "1": "i", "!": "i", "|": "i",
    "3": "e", "4": "a", "@": "a", "5": "s",
    "$": "s", "7": "t", "+": "t", "8": "b"
})

MULTILINGUAL_BAD_WORDS = [
    # Safe test keywords
    "test_badword", "testbadword", "dummy_badword", "test_link",

    # English
    "fuck", "fuk", "fck", "bitch", "asshole", "bastard", "dick",
    "pussy", "cunt", "slut", "whore", "motherfucker", "stfu",
    "nigger", "nigga", "faggot", "retard",

    # Hindi / Hinglish / Urdu
    "madarchod", "maderchod", "bhenchod", "behenchod", "bhosdike",
    "bsdk", "chutiya", "chutiye", "chutya", "laude", "lavde",
    "lodu", "gandu", "gaand", "lund", "randi", "harami", "kamina",
    "saale", "kutta", "kuttiya", "chod", "chudai", "bc", "mc",

    # Punjabi
    "kanjar", "fuddu", "fudu", "dalli", "dalle", "khotey", "paindu",

    # International (Spanish / Russian / French)
    "puta", "mierda", "cyka", "blyat", "kurwa", "merde"
]

# ==========================================================
# 2. ADULT / NSFW & LINK REGEX PATTERNS
# ==========================================================
ADULT_KEYWORDS = [
    "porn", "xxx", "nude", "nudes", "naked", "nsfw", "hentai",
    "boobs", "leaked", "mms", "onlyfans", "xvideos", "pornhub",
    "xnxx", "brazzers", "chaturbate", "stripchat", "rule34", "sex"
]

LINK_REGEX = re.compile(
    r"(?i)\b(https?://|www\.|t\.me/|telegram\.me/|[a-z0-9\-\.]+\.(com|org|net|xyz|in|ru|co|io|tk|me|top|site|club|vip|click))\b"
)

ALLOWED_MUSIC_REGEX = re.compile(
    r"(?i)\b(https?://(open\.spotify\.com|spotify\.com|www\.youtube\.com|youtube\.com|youtu\.be|music\.youtube\.com)/[^\s]+)\b"
)

def inspect_content(raw_text: str, topic_id: int, file_name: str = "") -> tuple[bool, str]:
    """Inspects text, media file names, and links for policy violations."""
    if not raw_text and not file_name:
        return False, ""

    full_check = f"{raw_text} {file_name}".lower()

    # 1. Adult / NSFW Keyword Check
    for ak in ADULT_KEYWORDS:
        if re.search(r"\b" + re.escape(ak) + r"\b", full_check):
            return True, "Adult/NSFW content"

    # 2. Profanity Check
    for bw in MULTILINGUAL_BAD_WORDS:
        if re.search(r"\b" + re.escape(bw) + r"\b", full_check):
            return True, "Bad language"

    # Leetspeak / normalized check
    normalized = full_check.translate(LEET_TRANS)
    normalized = re.sub(r"(.)\1{2,}", r"\1", normalized)
    for bw in MULTILINGUAL_BAD_WORDS:
        if re.search(r"\b" + re.escape(bw) + r"\b", normalized):
            return True, "Bad language"

    clean_nospace = re.sub(r"[\s\.\-_,\*#]+", "", normalized)
    for bw in MULTILINGUAL_BAD_WORDS:
        if len(bw) >= 3 and bw in clean_nospace:
            return True, "Bad language"

    # 3. Anti-Link / Anti-Spam Check
    if LINK_REGEX.search(raw_text):
        # In Music Topic, allow verified Spotify & YouTube music links
        if topic_id == MUSIC_TOPIC_ID:
            if ALLOWED_MUSIC_REGEX.search(raw_text):
                return False, ""
        return True, "Unauthorized links or spam"

    return False, ""

bot = TelegramClient(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "aegis_session"),
    API_ID,
    API_HASH
)

async def restrict_user(user_id, days=None):
    """Restricts a user for specified days, or permanently if days is None."""
    until = None
    if days is not None:
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)

    rights = ChatBannedRights(
        until_date=until,
        send_messages=True,
        send_media=True,
        send_stickers=True,
        send_gifs=True,
        send_games=True,
        send_inline=True,
        embed_links=True
    )
    await bot(EditBannedRequest(
        channel=GROUP_ID,
        participant=user_id,
        banned_rights=rights
    ))

async def unrestrict_user(user_id):
    """Lifts all chat restrictions and unblocks a user in the supergroup."""
    rights = ChatBannedRights(
        until_date=None,
        view_messages=False,
        send_messages=False,
        send_media=False,
        send_stickers=False,
        send_gifs=False,
        send_games=False,
        send_inline=False,
        embed_links=False,
        send_polls=False,
        change_info=False,
        invite_users=False,
        pin_messages=False
    )
    await bot(EditBannedRequest(
        channel=GROUP_ID,
        participant=user_id,
        banned_rights=rights
    ))

def parse_wake_command(text: str):
    """
    Parses natural wake/unmute commands without slash:
      wake @username
      wake username
      wake 8176379957
      wake all / wake everyone
      wake (when replying to a user's message)
      /wake, /unmute, /reset
    """
    if not text:
        return False, None
    m = re.match(r"^(?:/)?(?:wake|unmute|reset)(?:\s+(.+))?$", text.strip(), re.IGNORECASE)
    if not m:
        return False, None
    arg = m.group(1)
    if not arg:
        return True, ""  # Reply or help request
    arg = arg.strip()
    
    if " " in arg:
        if arg.lower() in ["all", "everyone", "all users", "all members"]:
            return True, "all"
        return False, None  # Normal sentence like "wake up bro", skip
        
    clean_arg = arg.lstrip("@")
    if clean_arg.lower() in ["all", "everyone"]:
        return True, "all"
        
    if re.match(r"^[a-zA-Z0-9_]{3,32}$", clean_arg) or clean_arg.isdigit():
        return True, clean_arg
        
    return False, None

async def resolve_target_user(query_str: str):
    """Resolves a username or ID string into an entity."""
    clean = query_str.lstrip("@").strip()
    
    # 1. Numeric ID
    if clean.isdigit():
        try:
            return await bot.get_entity(int(clean))
        except Exception:
            pass

    # 2. Direct get_entity
    try:
        return await bot.get_entity(clean)
    except Exception:
        pass

    # 3. Search in group participants
    try:
        search_res = await bot(GetParticipantsRequest(
            channel=GROUP_ID,
            filter=ChannelParticipantsSearch(clean),
            offset=0,
            limit=5,
            hash=0
        ))
        for u in search_res.users:
            if (u.username and u.username.lower() == clean.lower()) or (str(u.id) == clean):
                return u
        if len(search_res.users) > 0:
            return search_res.users[0]
    except Exception as e:
        print(f"[!] Error in participant search: {e}")

    return None

async def handle_wake(event, sender, arg):
    """Handles admin wake command to unmute members and reset strikes."""
    # 1. Verify admin permissions
    is_admin = False
    try:
        perms = await bot.get_permissions(GROUP_ID, sender.id)
        if perms.is_admin:
            is_admin = True
    except Exception:
        pass

    if not is_admin:
        alert = await event.reply("⚠️ **Permission Denied:** Only group admins can wake or unmute members.")
        await asyncio.sleep(4)
        try:
            await alert.delete()
        except Exception:
            pass
        return

    # 2. WAKE ALL / EVERYONE
    if arg == "all":
        try:
            banned = await bot(GetParticipantsRequest(
                channel=GROUP_ID,
                filter=ChannelParticipantsBanned(q=""),
                offset=0,
                limit=100,
                hash=0
            ))
            count = 0
            for p in banned.participants:
                try:
                    await unrestrict_user(p.peer.user_id)
                    count += 1
                except Exception:
                    pass
            save_records({})
            await event.reply(
                f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
                f"🕊️ **All Restrictions Lifted!**\n"
                f"• Restored Members: **{count}**\n"
                f"• Strikes: **All Reset to 0 (Clean Slate)** ✨"
            )
        except Exception as e:
            await event.reply(f"❌ Error lifting restrictions: {e}")
        return

    # 3. WAKE VIA REPLY
    target_user = None
    target_id = None
    if arg == "":
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                target_user = await reply_msg.get_sender()
                if target_user:
                    target_id = target_user.id
        if not target_id:
            await event.reply(
                "⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
                "ℹ️ **How to Wake / Unmute:**\n"
                "• `wake @username` — Unmutes by username\n"
                "• `wake <username>` — e.g. `wake ComOunEvan`\n"
                "• Reply to someone's message with `wake`\n"
                "• `wake all` — Unmutes all restricted members"
            )
            return

    # 4. WAKE VIA USERNAME OR USER ID
    if not target_id and arg:
        target_user = await resolve_target_user(arg)
        if target_user:
            target_id = target_user.id
        elif arg.isdigit():
            target_id = int(arg)
        else:
            await event.reply(
                f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
                f"⚠️ Could not find user `@{arg}` in this group.\n"
                f"Please check the username spelling or reply to their message with `wake`."
            )
            return

    # 5. EXECUTE UNRESTRICT & RESET RECORDS
    try:
        await unrestrict_user(target_id)
    except Exception as e:
        print(f"[!] Unrestrict API note: {e}")

    records = load_records()
    records.pop(str(target_id), None)
    save_records(records)

    name = target_user.first_name if (target_user and target_user.first_name) else f"User {target_id}"
    uname_str = f" (@{target_user.username})" if (target_user and target_user.username) else ""

    await event.reply(
        f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
        f"🕊️ [{name}](tg://user?id={target_id}){uname_str} has been awakened & unmuted!\n\n"
        f"• **Status:** Clean Slate ✨\n"
        f"• **Warnings:** 0 / 3\n"
        f"• **Restrictions:** Completely Lifted\n\n"
        f"Welcome back to the conversation! 🙌"
    )

@bot.on(events.NewMessage(chats=GROUP_ID))
async def monitor_messages(event):
    sender = await event.get_sender()
    if not sender or sender.bot:
        return

    text = event.raw_text or ""
    file_name = ""
    if event.file and hasattr(event.file, "name") and event.file.name:
        file_name = event.file.name

    # Check for natural wake / unmute admin commands first
    is_wake, wake_arg = parse_wake_command(text)
    if is_wake:
        await handle_wake(event, sender, wake_arg)
        return

    # Determine topic ID if threaded
    topic_id = 0
    if event.message.reply_to:
        reply_header = event.message.reply_to
        topic_id = reply_header.reply_to_top_id or reply_header.reply_to_msg_id or 0

    violation, reason = inspect_content(text, topic_id, file_name)
    if not violation:
        return

    is_test_trigger = any(k in text.lower() for k in ["test_badword", "testbadword", "dummy_badword", "test_link"])
    is_admin = False

    # Check if sender is an admin
    try:
        perms = await bot.get_permissions(GROUP_ID, sender.id)
        if perms.is_admin:
            is_admin = True
            if not is_test_trigger:
                return  # Admins are exempt from regular moderation
    except Exception:
        pass

    # 1. Immediately delete the violating message
    try:
        await event.delete()
    except Exception as e:
        print(f"[!] Could not delete message: {e}")

    user_id_str = str(sender.id)
    name = sender.first_name or "Friend"
    records = load_records()

    user_data = records.get(user_id_str, {
        "warnings": 0,
        "stage": 0,
        "last_violation": None
    })

    stage = user_data.get("stage", 0)
    warnings = user_data.get("warnings", 0)

    # -------------------------------------------------------------
    # STAGE 0: Initial ladder (3 warnings, 4th = 24h detention)
    # -------------------------------------------------------------
    if stage == 0:
        warnings += 1
        user_data["warnings"] = warnings
        user_data["last_violation"] = str(datetime.datetime.now(datetime.timezone.utc))

        if warnings <= 3:
            records[user_id_str] = user_data
            save_records(records)

            notice = await event.respond(
                f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
                f"Hey [{name}](tg://user?id={sender.id})! {reason} is strictly prohibited here.\n\n"
                f"⚠️ **Warning: {warnings} of 3**\n"
                f"📌 If you reach 4 warnings, you will be muted for 24 hours."
            )
            await asyncio.sleep(6)
            try:
                await notice.delete()
            except Exception:
                pass
            return

        elif warnings >= 4:
            user_data["stage"] = 1
            user_data["warnings"] = 0
            records[user_id_str] = user_data
            save_records(records)

            if not is_admin:
                await restrict_user(sender.id, days=1)

            notice = await event.respond(
                f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
                f"🛑 **You are muted for 24 hours!**\n\n"
                f"[{name}](tg://user?id={sender.id}), you reached 4 warnings for breaking community rules.\n"
                f"Please take a 1-day break to cool down."
            )
            await asyncio.sleep(8)
            try:
                await notice.delete()
            except Exception:
                pass
            return

    # -------------------------------------------------------------
    # STAGE 1: Repeat violation after 1-day detention -> 48h mute
    # -------------------------------------------------------------
    elif stage == 1:
        user_data["stage"] = 2
        user_data["last_violation"] = str(datetime.datetime.now(datetime.timezone.utc))
        records[user_id_str] = user_data
        save_records(records)

        if not is_admin:
            await restrict_user(sender.id, days=2)

        notice = await event.respond(
            f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
            f"🛑 **You are muted for 48 hours (2 Days)!**\n\n"
            f"[{name}](tg://user?id={sender.id}), you broke community rules again after your previous mute.\n"
            f"No warnings this time. One more violation will be permanent."
        )
        await asyncio.sleep(8)
        try:
            await notice.delete()
        except Exception:
            pass
        return

    # -------------------------------------------------------------
    # STAGE 2: Repeat violation after 48h detention -> Permanent Ban
    # -------------------------------------------------------------
    elif stage >= 2:
        user_data["stage"] = 3
        user_data["last_violation"] = str(datetime.datetime.now(datetime.timezone.utc))
        records[user_id_str] = user_data
        save_records(records)

        if not is_admin:
            await restrict_user(sender.id, days=None)

        notice = await event.respond(
            f"⚡ **𝐀 Ｅ Ｇ Ｉ Ｓ** ⚡\n\n"
            f"⛔ **Permanently Muted!**\n\n"
            f"[{name}](tg://user?id={sender.id}), you continued breaking rules after multiple mutes.\n"
            f"Your chat access has been permanently removed."
        )
        return

async def main():
    print("=" * 65)
    print("      ⚡ AEGIS 360 SHIELD (PROFANITY + NSFW + ANTI-LINK) ⚡       ")
    print("=" * 65)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"[+] Bot Online: @{me.username} ({me.first_name})")
    print(f"[+] Guarding Group: {GROUP_ID}")
    print("[+] All Shields Active: Bad Words, NSFW Media, Spam Links.")
    print("[+] Wake & Unmute Command Enabled (wake <username>, wake all, etc.)\n")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAegis stopped.")
