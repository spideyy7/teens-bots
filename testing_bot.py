import asyncio
import os
import json
from telethon import TelegramClient, events
from telethon.tl.functions.channels import EditBannedRequest, GetParticipantsRequest
from telethon.tl.types import ChatBannedRights, ChannelParticipantsKicked, ChannelParticipantsSearch

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
BOT_TOKEN = "9095806512:AAGHI10xphU4laEBMddr7GtznzIkYsa-4AU"
GROUP_ID = -1003943756594
AEGIS_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aegis_records.json")

bot = TelegramClient(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "testing_bot_session"),
    API_ID,
    API_HASH
)

def get_aegis_records():
    if os.path.exists(AEGIS_DATA_FILE):
        try:
            with open(AEGIS_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

@bot.on(events.NewMessage(pattern=r"^/help"))
async def cmd_help(event):
    help_text = (
        "🧪 **Testing Bot — Diagnostic & Test Suite**\n\n"
        "Commands:\n"
        "• `/status` — System health check\n"
        "• `/strikes` [@username] — Check warnings for yourself or a user\n"
        "• `wake <username>` — Lift mute and reset strikes (Managed by @AegisGuardModerationBot)\n"
        "• `/unban_all` — Lift ban on all kicked/removed members\n"
    )
    await event.reply(help_text)

@bot.on(events.NewMessage(pattern=r"^/status"))
async def cmd_status(event):
    status_text = (
        "🟢 **System Status: Operational**\n\n"
        "• **Gatekeeper:** Active (@TeensGate_6836_bot)\n"
        "• **Aegis Shield:** Active (@AegisGuardModerationBot)\n"
        "• **Music Engine:** Active (@TeensMusic_6860_bot)\n"
        "• **Testing Suite:** Active (@TeensTesting_248_bot)\n"
        "• **Guarded Group:** Teens 💗 (-1003943756594)\n"
    )
    await event.reply(status_text)

@bot.on(events.NewMessage(pattern=r"^/strikes"))
async def cmd_strikes(event):
    target_id = None
    target_name = None
    if event.is_reply:
        reply_msg = await event.get_reply_message()
        if reply_msg:
            target_id = reply_msg.sender_id
            sender_ent = await reply_msg.get_sender()
            if sender_ent:
                target_name = sender_ent.first_name
    else:
        parts = event.raw_text.split()
        if len(parts) > 1:
            query = parts[1].lstrip("@").strip()
            if query.isdigit():
                target_id = int(query)
            else:
                try:
                    u = await bot.get_entity(query)
                    target_id = u.id
                    target_name = u.first_name
                except Exception:
                    try:
                        res = await bot(GetParticipantsRequest(
                            channel=GROUP_ID,
                            filter=ChannelParticipantsSearch(query),
                            offset=0,
                            limit=5,
                            hash=0
                        ))
                        for u in res.users:
                            if u.username and u.username.lower() == query.lower():
                                target_id = u.id
                                target_name = u.first_name
                                break
                    except Exception:
                        pass

    if not target_id:
        target_id = event.sender_id
        target_name = "You"

    records = get_aegis_records()
    user_data = records.get(str(target_id), {"warnings": 0, "stage": 0})
    
    stage_desc = {
        0: "Clean (0-3 Warnings)",
        1: "Stage 1 (Served 24h detention)",
        2: "Stage 2 (Served 48h detention)",
        3: "Stage 3 (Permanently restricted)"
    }.get(user_data.get("stage", 0), "Unknown")

    display_name = target_name or f"User {target_id}"
    res = (
        f"📋 **Aegis Record for {display_name} (`{target_id}`):**\n"
        f"• **Current Warnings:** {user_data.get('warnings', 0)}/3\n"
        f"• **Detention Stage:** {stage_desc}\n"
    )
    await event.reply(res)

@bot.on(events.NewMessage(pattern=r"^/unban_all"))
async def cmd_unban_all(event):
    kicked = await bot(GetParticipantsRequest(
        channel=GROUP_ID,
        filter=ChannelParticipantsKicked(q=""),
        offset=0,
        limit=50,
        hash=0
    ))

    count = 0
    for p in kicked.participants:
        try:
            await bot(EditBannedRequest(
                channel=GROUP_ID,
                participant=p.peer.user_id,
                banned_rights=ChatBannedRights(until_date=None, view_messages=False)
            ))
            count += 1
        except Exception:
            pass

    await event.reply(f"✅ Unbanned {count} kicked users successfully!")

async def main():
    print("=" * 60)
    print("        TESTING BOT - DIAGNOSTICS & TEST SUITE         ")
    print("=" * 60)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"[+] Bot Online: @{me.username} ({me.first_name})")
    print("[+] Ready for diagnostic commands.\n")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTesting bot stopped.")
