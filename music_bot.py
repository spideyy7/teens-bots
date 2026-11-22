"""
Teens 💗 Pure Inline & Topic 5 Music Bot (aiogram 3)
Features:
1. Pure Inline Search (@teenmusicbot <song>) with instant suggestions & caching
2. Strict Topic 5 (Songs Topic) enforcement: songs are strictly sent to Topic 5 only!
"""
import asyncio
import logging
import os
import sqlite3
import tempfile
import time
import aiohttp
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile, CallbackQuery, ChosenInlineResult, FSInputFile,
    InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery,
    InlineQueryResultArticle, InlineQueryResultCachedAudio,
    InputTextMessageContent, Message,
)
from dotenv import load_dotenv
from ytmusicapi import YTMusic
from yt_dlp import YoutubeDL

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.environ.get("BOT_TOKEN", "9075299775:AAHn0hotK_opSCXB-UKO6-G7dxkv8J3OwGg")
STORAGE_CHAT_ID = int(os.environ.get("STORAGE_CHAT_ID", "-1004422447055"))
GROUP_ID = -1003943756594
SONGS_TOPIC_ID = 5
SONGS_TOPIC_LINK = "https://t.me/c/3943756594/5"

DL_DIR = tempfile.mkdtemp(prefix="musicbot_")
MAX_PARALLEL_DOWNLOADS = 4

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("musicbot")

router = Router()
ytm = YTMusic()
dl_sem = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
inflight: dict[str, asyncio.Future] = {}
search_cache: dict[str, tuple[float, list]] = {}
prefetch_tasks: dict[int, asyncio.Task] = {}

# Storage SQLite
DB_PATH = os.path.join(BASE_DIR, "cache.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS songs (vid TEXT PRIMARY KEY, file_id TEXT, title TEXT, artist TEXT, duration INT)")
db.commit()

def cached(vid: str):
    return db.execute("SELECT file_id, title, artist, duration FROM songs WHERE vid=?", (vid,)).fetchone()

def save(vid, file_id, title, artist, duration):
    db.execute("INSERT OR REPLACE INTO songs VALUES (?,?,?,?,?)", (vid, file_id, title, artist, duration))
    db.commit()

def _search_sync(q: str) -> list[dict]:
    res = ytm.search(q, filter="songs", limit=8)
    out = []
    for r in res:
        if not r.get("videoId"):
            continue
        out.append({
            "id": r["videoId"],
            "title": r["title"],
            "artist": ", ".join(a["name"] for a in r.get("artists", [])) or "Unknown",
            "dur": r.get("duration_seconds") or 0,
            "album": (r.get("album") or {}).get("name", ""),
        })
    return out

async def search(q: str) -> list[dict]:
    q = q.strip().lower()
    hit = search_cache.get(q)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    res = await asyncio.to_thread(_search_sync, q)
    search_cache[q] = (time.time(), res)
    if len(search_cache) > 2000:
        search_cache.pop(next(iter(search_cache)))
    return res

def thumb(vid: str) -> str:
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"

def fmt_dur(s: int) -> str:
    return f"{s // 60}:{s % 60:02d}"

def _download_sync(vid: str) -> str:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": f"{DL_DIR}/%(id)s.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "max_filesize": 48 * 1024 * 1024,
        "concurrent_fragment_downloads": 4,
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "web_embedded", "android", "ios"],
                "player_skip": ["webpage"]
            }
        }
    }
    with YoutubeDL(opts) as y:
        info = y.extract_info(f"https://music.youtube.com/watch?v={vid}", download=True)
        return y.prepare_filename(info)

async def _fetch_thumb(vid: str):
    try:
        async with aiohttp.ClientSession() as s, s.get(thumb(vid), timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status == 200:
                return BufferedInputFile(await r.read(), "t.jpg")
    except Exception:
        pass
    return None

async def _prepare(bot: Bot, song: dict) -> str:
    async with dl_sem:
        path = await asyncio.to_thread(_download_sync, song["id"])
        try:
            msg = await bot.send_audio(
                STORAGE_CHAT_ID, FSInputFile(path),
                title=song["title"], performer=song["artist"],
                duration=song["dur"], thumbnail=await _fetch_thumb(song["id"]),
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    fid = msg.audio.file_id
    save(song["id"], fid, song["title"], song["artist"], song["dur"])
    return fid

async def get_file_id(bot: Bot, song: dict) -> str:
    row = cached(song["id"])
    if row:
        return row[0]
    fut = inflight.get(song["id"])
    if fut is None:
        fut = asyncio.ensure_future(_prepare(bot, song))
        inflight[song["id"]] = fut
        fut.add_done_callback(lambda _: inflight.pop(song["id"], None))
    return await asyncio.shield(fut)

async def song_from_id(vid: str) -> dict | None:
    row = cached(vid)
    if row:
        return {"id": vid, "title": row[1], "artist": row[2], "dur": row[3]}
    try:
        r = await asyncio.to_thread(ytm.get_song, vid)
        d = r["videoDetails"]
        return {"id": vid, "title": d["title"], "artist": d["author"], "dur": int(d["lengthSeconds"])}
    except Exception:
        return None

# ───────────────────────────── inline mode ─────────────────────────────
@router.inline_query()
async def on_inline(q: InlineQuery, bot: Bot):
    text = q.query.strip()
    if text.lower().startswith("play "):
        text = text[5:].strip()
    if len(text) < 2:
        await q.answer([], cache_time=1, switch_pm_text="Type a song name…", switch_pm_parameter="help")
        return

    songs = await search(text)
    results = []
    for s in songs:
        desc = f"{s['artist']} • {fmt_dur(s['dur'])}"
        row = cached(s["id"])
        if row:
            # Cached in storage -> instant native audio bubble!
            results.append(InlineQueryResultCachedAudio(id=s["id"], audio_file_id=row[0]))
        else:
            results.append(InlineQueryResultArticle(
                id=s["id"],
                title=s["title"],
                description=desc,
                thumbnail_url=thumb(s["id"]),
                input_message_content=InputTextMessageContent(
                    message_text=(
                        f"🎧 <b>{s['title']}</b>\n"
                        f"👤 {s['artist']} • ⏱️ {fmt_dur(s['dur'])}\n\n"
                        f"🎵 <i>Click below to play in 🎵 Songs topic:</i>"
                    ),
                    parse_mode="HTML"
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="▶️ Play in 🎵 Songs Topic", url=f"https://t.me/teenmusicbot?start=p_{s['id']}")
                ]]),
            ))
    await q.answer(results, cache_time=5, is_personal=False)

    # Pre-warm cache for the top result
    if songs and not cached(songs[0]["id"]):
        old = prefetch_tasks.pop(q.from_user.id, None)
        if old:
            old.cancel()

        async def _warm(song=songs[0]):
            await asyncio.sleep(1.0)
            try:
                await get_file_id(bot, song)
            except Exception:
                pass

        prefetch_tasks[q.from_user.id] = asyncio.create_task(_warm())


@router.chosen_inline_result()
async def on_chosen(c: ChosenInlineResult, bot: Bot):
    song = await song_from_id(c.result_id)
    if song:
        try:
            await get_file_id(bot, song)
        except Exception:
            pass


# ───────────────────────────── Topic 5 / Direct Commands ─────────────────────────────
@router.message(CommandStart())
async def start(m: Message, bot: Bot):
    args = m.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("p_"):
        vid = args[1][2:]
        song = await song_from_id(vid)
        if song:
            # Deliver directly to Topic 5 in Teens 💗
            wait_msg = await bot.send_message(
                GROUP_ID,
                f"⚡ Fetching <b>{song['title']}</b> for 🎵 Songs topic...",
                message_thread_id=SONGS_TOPIC_ID,
                parse_mode="HTML"
            )
            try:
                fid = await get_file_id(bot, song)
                await bot.send_audio(
                    chat_id=GROUP_ID,
                    audio=fid,
                    title=song["title"],
                    performer=song["artist"],
                    duration=song["dur"],
                    message_thread_id=SONGS_TOPIC_ID
                )
                try:
                    await wait_msg.delete()
                except Exception:
                    pass
                if m.chat.type == "private":
                    await m.answer(f"✅ Delivered <b>{song['title']}</b> to 🎵 Songs topic!", parse_mode="HTML")
            except Exception as e:
                await wait_msg.edit_text(f"❌ Could not load song: {e}")
            return

    me = await bot.get_me()
    await m.answer(
        "🎵 <b>Teens Music Bot</b>\n\n"
        f"• In any chat, type <code>@{me.username} song name</code> for live inline search\n"
        f"• Songs are strictly delivered to <b>🎵 Songs</b> (Section 5) in Teens 💗 group!",
        parse_mode="HTML"
    )


# ───────────────────────────── "play <song>" in group ─────────────────────────────
@router.message(F.text.regexp(r"(?i)^/?play(@\w+)?\s+.+"))
async def play_cmd(m: Message, bot: Bot):
    # Check if in Group and NOT in Topic 5!
    if m.chat.id == GROUP_ID and m.message_thread_id != SONGS_TOPIC_ID:
        name = m.from_user.first_name or "Friend"
        warn = await m.reply(
            f"⚠️ [{name}](tg://user?id={m.from_user.id}), music is strictly allowed in 🎵 **Songs** section only!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="👉 Go to 🎵 Songs Topic", url=SONGS_TOPIC_LINK)
            ]])
        )
        await asyncio.sleep(5)
        try:
            await warn.delete()
            await m.delete()
        except Exception:
            pass
        return

    query = m.text.split(maxsplit=1)[1]
    songs = await search(query)
    if not songs:
        return await m.reply("😕 Nothing found.")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'⚡ ' if cached(s['id']) else ''}{s['title']} — {s['artist']} ({fmt_dur(s['dur'])})"[:60],
            callback_data=f"p:{s['id']}"
        )]
        for s in songs[:6]
    ])
    await m.reply(f"🔎 Results for <b>{query}</b>", parse_mode="HTML", reply_markup=keyboard)
    if not cached(songs[0]["id"]):
        asyncio.create_task(get_file_id(bot, songs[0]))


@router.callback_query(F.data.startswith("p:"))
async def pick(c: CallbackQuery, bot: Bot):
    # Enforce Topic 5 in group
    if c.message.chat.id == GROUP_ID and c.message.message_thread_id != SONGS_TOPIC_ID:
        await c.answer("⚠️ Music is strictly allowed in 🎵 Songs topic only!", show_alert=True)
        return

    vid = c.data[2:]
    await c.answer("⚡ Sending song…")
    song = await song_from_id(vid)
    if not song:
        return
    
    target_chat = GROUP_ID if c.message.chat.id == GROUP_ID else c.message.chat.id
    target_thread = SONGS_TOPIC_ID if c.message.chat.id == GROUP_ID else None
    
    try:
        fid = await get_file_id(bot, song)
        await bot.send_audio(
            chat_id=target_chat,
            audio=fid,
            title=song["title"],
            performer=song["artist"],
            duration=song["dur"],
            message_thread_id=target_thread,
            reply_to_message_id=c.message.message_id
        )
    except Exception as e:
        log.exception("Error sending song: %s", e)
        await bot.send_message(
            chat_id=target_chat,
            text=f"❌ <b>Could not download song:</b> {e}",
            message_thread_id=target_thread,
            parse_mode="HTML"
        )
