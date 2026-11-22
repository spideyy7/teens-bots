"""
=============================================================================
   TEENS 💗 UNIFIED RENDER CLOUD ENGINE
   Runs all 4 bots + Web Health Check Server in a single async event loop:
   1. Gatekeeper Bot (@TeensGate_6836_bot)
   2. Aegis Peacekeeper (@AegisGuardModerationBot)
   3. Testing Bot (@TeensTesting_248_bot)
   4. Teens Music Bot (@teenmusicbot)
   5. Health Check Web Server (for Render on $PORT)
=============================================================================
"""
import asyncio
import os
import sys
import time
from aiohttp import web

# Import Bot Services
import bot_service
import aegis_bot
import testing_bot
import music_bot

START_TIME = time.time()
PORT = int(os.environ.get("PORT", 10000))

# ── Health Check Web Handler for Render ────────────────────
async def handle_home(request):
    uptime_sec = int(time.time() - START_TIME)
    return web.Response(
        text=(
            "<h1>Teens &#128151; Bots Server is 100% ONLINE!</h1>"
            f"<p><b>Uptime:</b> {uptime_sec}s</p>"
            "<ul>"
            "<li>Gatekeeper (@TeensGate_6836_bot) &mdash; Active</li>"
            "<li>Aegis Peacekeeper (@AegisGuardModerationBot) &mdash; Active</li>"
            "<li>Testing Bot (@TeensTesting_248_bot) &mdash; Active</li>"
            "<li>Teens Music (@teenmusicbot) &mdash; Active</li>"
            "</ul>"
        ),
        content_type="text/html"
    )

async def handle_health(request):
    return web.json_response({
        "status": "healthy",
        "uptime": int(time.time() - START_TIME),
        "bots": {
            "gatekeeper": "online",
            "aegis": "online",
            "tester": "online",
            "music": "online"
        }
    })

def make_web_app():
    app = web.Application()
    app.router.add_get("/", handle_home)
    app.router.add_get("/health", handle_health)
    return app

async def start_web_server():
    app = make_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[✓] Render Health Web Server listening on port {PORT}", flush=True)

# ── Main Unified Async Loop ────────────────────────────────
async def main():
    print("=" * 65, flush=True)
    print("      🚀 STARTING TEENS 💗 UNIFIED CLOUD SERVER (RENDER) 🚀      ", flush=True)
    print("=" * 65, flush=True)

    # 1. Start Render Web Server
    await start_web_server()

    # 2. Start Group Security Bots (Telethon)
    print("[+] Starting Gatekeeper (@TeensGate_6836_bot)...", flush=True)
    await bot_service.bot.start(bot_token=bot_service.BOT_TOKEN)
    gate_me = await bot_service.bot.get_me()
    print(f"[✓] Gatekeeper Online: @{gate_me.username}", flush=True)

    print("[+] Starting Aegis Peacekeeper (@AegisGuardModerationBot)...", flush=True)
    await aegis_bot.bot.start(bot_token=aegis_bot.BOT_TOKEN)
    aegis_me = await aegis_bot.bot.get_me()
    print(f"[✓] Aegis Online: @{aegis_me.username}", flush=True)

    print("[+] Starting Testing Bot (@TeensTesting_248_bot)...", flush=True)
    await testing_bot.bot.start(bot_token=testing_bot.BOT_TOKEN)
    test_me = await testing_bot.bot.get_me()
    print(f"[✓] Testing Bot Online: @{test_me.username}", flush=True)

    # 3. Start Teens Music Bot (Aiogram 3)
    print("[+] Starting Teens Music Bot (@teenmusicbot)...", flush=True)
    aiogram_bot = music_bot.Bot(music_bot.BOT_TOKEN)
    music_me = await aiogram_bot.get_me()
    print(f"[✓] Music Bot Online: @{music_me.username}", flush=True)

    dp = music_bot.Dispatcher()
    dp.include_router(music_bot.router)

    print("\n[🎉] ALL 4 BOTS & WEB SERVER ARE SYNCHRONIZED AND LIVE!\n", flush=True)

    # Run polling for aiogram and telethon concurrently
    await asyncio.gather(
        dp.start_polling(aiogram_bot, allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"]),
        bot_service.bot.run_until_disconnected(),
        aegis_bot.bot.run_until_disconnected(),
        testing_bot.bot.run_until_disconnected()
    )

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\nServer stopped by admin.")
            break
        except Exception as e:
            print(f"\n[!] Server exception: {e}. Auto-restarting in 3s...", flush=True)
            time.sleep(3)
