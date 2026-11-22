"""
Fast Telegram music bot (aiogram 3)

Features
1. Inline search with live suggestions  ->  type  @yourbot <song>  in any chat
   Also works in groups: write  play <song>  and tap a suggestion button.
2. Instant delivery: every song is uploaded to Telegram ONCE (file_id cached
   in SQLite). Cached songs are sent instantly, the top result is pre-fetched
   while the user is still looking at the list.
3. Voice / humming recognition: send a voice note with caption  find
   (or reply to a voice note with  find ).  ACRCloud (supports humming) first,
   Shazam as a fallback for real recordings.
"""
import asyncio
import base64
import hashlib
import hmac
import logging
import os
import sqlite3
import tempfile
import time

import aiohttp
import sys

# Ensure ffmpeg in virtualenv Scripts is on PATH
_scripts_dir = os.path.dirname(sys.executable)
if _scripts_dir not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _scripts_dir + os.pathsep + os.environ.get("PATH", "")

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile, CallbackQuery, ChosenInlineResult, FSInputFile,
    InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery,
    InlineQueryResultArticle, InlineQueryResultCachedAudio,
    InputMediaAudio, InputTextMessageContent, Message,
)
from dotenv import load_dotenv
from ytmusicapi import YTMusic
from yt_dlp import YoutubeDL

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
BOT_TOKEN = os.environ["BOT_TOKEN"]
STORAGE_CHAT_ID = int(os.environ["STORAGE_CHAT_ID"])  # private channel/group where the bot is admin
ACR_HOST = os.getenv("ACR_HOST", "")                  # e.g. identify-eu-west-1.acrcloud.com
ACR_KEY = os.getenv("ACR_ACCESS_KEY", "")
ACR_SECRET = os.getenv("ACR_ACCESS_SECRET", "")

DL_DIR = tempfile.mkdtemp(prefix="musicbot_")
MAX_PARALLEL_DOWNLOADS = 4

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("musicbot")

router = Router()
ytm = YTMusic()
dl_sem = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
inflight: dict[str, asyncio.Future] = {}      # video_id -> future (no duplicate downloads)
search_cache: dict[str, tuple[float, list]] = {}
prefetch_tasks: dict[int, asyncio.Task] = {}  # user_id -> pending prefetch


# ───────────────────────── storage (file_id cache) ─────────────────────────
DB_PATH = os.path.join(BASE_DIR, "cache.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS songs (vid TEXT PRIMARY KEY, file_id TEXT, "
           "title TEXT, artist TEXT, duration INT)")
db.commit()


def cached(vid: str):
    return db.execute("SELECT file_id, title, artist, duration FROM songs WHERE vid=?", (vid,)).fetchone()


def save(vid, file_id, title, artist, duration):
    db.execute("INSERT OR REPLACE INTO songs VALUES (?,?,?,?,?)", (vid, file_id, title, artist, duration))
    db.commit()


# ───────────────────────────── search ─────────────────────────────
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


# ───────────────────────────── download + upload ─────────────────────────────
def _download_sync(vid: str) -> str:
    opts = {
        # m4a plays natively in Telegram's audio player and needs NO ffmpeg conversion = fast
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
    """Download -> upload to storage chat -> return file_id (cached forever)."""
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
            # already uploaded -> tapping sends the audio INSTANTLY
            results.append(InlineQueryResultCachedAudio(id=s["id"], audio_file_id=row[0]))
        else:
            results.append(InlineQueryResultArticle(
                id=s["id"], title=s["title"], description=desc, thumbnail_url=thumb(s["id"]),
                input_message_content=InputTextMessageContent(
                    message_text=f"🎧 <b>{s['title']}</b>\n{s['artist']} • {fmt_dur(s['dur'])}\n\n▶️ <i>Tap below to receive song audio:</i>",
                    parse_mode="HTML"
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🎵 Play / Get Song", url=f"https://t.me/teenmusicbot?start=p_{s['id']}")
                ]]),
            ))
    await q.answer(results, cache_time=5, is_personal=False)

    # warm the cache for the top result once the user pauses typing (~1.2s)
    if songs and not cached(songs[0]["id"]):
        old = prefetch_tasks.pop(q.from_user.id, None)
        if old:
            old.cancel()

        async def _warm(song=songs[0]):
            await asyncio.sleep(1.2)
            try:
                await get_file_id(bot, song)
            except Exception as e:
                log.warning("prefetch failed: %s", e)

        prefetch_tasks[q.from_user.id] = asyncio.create_task(_warm())


@router.chosen_inline_result()
async def on_chosen(c: ChosenInlineResult, bot: Bot):
    song = await song_from_id(c.result_id)
    if song:
        try:
            await get_file_id(bot, song)
        except Exception as e:
            log.warning("Warm caching failed for chosen song: %s", e)


@router.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer("Almost ready…")


# ───────────────────────────── "play <song>" in groups ─────────────────────────────
def results_keyboard(songs: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'⚡ ' if cached(s['id']) else ''}{s['title']} — {s['artist']} ({fmt_dur(s['dur'])})"[:60],
                              callback_data=f"p:{s['id']}")]
        for s in songs[:6]
    ])


@router.message(CommandStart())
async def start(m: Message, bot: Bot):
    args = m.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("p_"):
        vid = args[1][2:]
        song = await song_from_id(vid)
        if song:
            wait_msg = await m.answer(f"⚡ Loading <b>{song['title']}</b>...", parse_mode="HTML")
            try:
                fid = await get_file_id(bot, song)
                await m.answer_audio(fid, title=song["title"], performer=song["artist"], duration=song["dur"])
                try:
                    await wait_msg.delete()
                except Exception:
                    pass
            except Exception as e:
                await wait_msg.edit_text(f"❌ Could not deliver song: {e}")
            return

    me = await bot.get_me()
    await m.answer(
        "🎵 <b>Music Bot</b>\n\n"
        f"• Type <code>@{me.username} song name</code> in any chat for live suggestions\n"
        "• In a group write <code>play song name</code>\n"
        "• Send a voice note of you humming/singing with caption <code>find</code>",
        parse_mode="HTML")


@router.message(F.text.regexp(r"(?i)^/?play(@\w+)?\s+.+"))
async def play_cmd(m: Message, bot: Bot):
    query = m.text.split(maxsplit=1)[1]
    songs = await search(query)
    if not songs:
        return await m.reply("😕 Nothing found.")
    await m.reply(f"🔎 Results for <b>{query}</b>", parse_mode="HTML", reply_markup=results_keyboard(songs))
    if not cached(songs[0]["id"]):
        asyncio.create_task(get_file_id(bot, songs[0]))  # warm top result


@router.callback_query(F.data.startswith("p:"))
async def pick(c: CallbackQuery, bot: Bot):
    vid = c.data[2:]
    await c.answer("⚡ Sending song…")
    song = await song_from_id(vid)
    if not song:
        return
    thread_id = getattr(c.message, "message_thread_id", None)
    rep_id = c.message.reply_to_message.message_id if c.message.reply_to_message else c.message.message_id
    try:
        fid = await get_file_id(bot, song)
        await bot.send_audio(
            chat_id=c.message.chat.id,
            audio=fid,
            title=song["title"],
            performer=song["artist"],
            duration=song["dur"],
            message_thread_id=thread_id,
            reply_to_message_id=rep_id
        )
    except Exception as e:
        log.exception("Error delivering song: %s", e)
        await bot.send_message(
            chat_id=c.message.chat.id,
            text=f"❌ <b>Could not download song:</b> {e}",
            message_thread_id=thread_id,
            reply_to_message_id=rep_id,
            parse_mode="HTML"
        )


# ───────────────────────────── voice / humming recognition ─────────────────────────────
async def to_wav(src: str) -> str:
    dst = src + ".wav"
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", src, "-t", "20", "-ac", "1", "-ar", "16000", dst,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await p.wait()
    return dst


async def acr_identify(wav_path: str) -> str | None:
    if not (ACR_HOST and ACR_KEY and ACR_SECRET):
        return None
    ts = str(int(time.time()))
    to_sign = "\n".join(["POST", "/v1/identify", ACR_KEY, "audio", "1", ts])
    sig = base64.b64encode(hmac.new(ACR_SECRET.encode(), to_sign.encode(), hashlib.sha1).digest()).decode()
    data = open(wav_path, "rb").read()
    form = aiohttp.FormData()
    for k, v in {"access_key": ACR_KEY, "data_type": "audio", "signature_version": "1",
                 "signature": sig, "timestamp": ts, "sample_bytes": str(len(data))}.items():
        form.add_field(k, v)
    form.add_field("sample", data, filename="s.wav", content_type="audio/wav")
    async with aiohttp.ClientSession() as s, s.post(f"https://{ACR_HOST}/v1/identify", data=form) as r:
        j = await r.json(content_type=None)
    if j.get("status", {}).get("code") != 0:
        return None
    meta = j["metadata"]
    hit = (meta.get("humming") or meta.get("music") or [None])[0]
    if not hit:
        return None
    return f"{hit['title']} {hit['artists'][0]['name']}"


async def shazam_identify(path: str) -> str | None:
    try:
        from shazamio import Shazam
        out = await Shazam().recognize(path)
        t = out.get("track")
        return f"{t['title']} {t['subtitle']}" if t else None
    except Exception:
        return None


async def lyrics_identify(wav_path: str) -> str | None:
    """Human voice & lyrics speech-to-text recognition (Hindi, Hinglish & English)."""
    def _recognize_sync():
        try:
            import speech_recognition as sr
            rec = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio = rec.record(source)
            # 1. Try Hindi (recognizes Hindi/Bollywood lyrics)
            try:
                text = rec.recognize_google(audio, language="hi-IN")
                if text and len(text.strip()) > 2:
                    return text.strip()
            except Exception:
                pass
            # 2. Try English
            try:
                text = rec.recognize_google(audio, language="en-US")
                if text and len(text.strip()) > 2:
                    return text.strip()
            except Exception:
                pass
        except Exception as e:
            log.warning("Lyrics voice recognition note: %s", e)
        return None
    return await asyncio.to_thread(_recognize_sync)


async def recognize(m: Message, bot: Bot, voice_msg: Message):
    status = await m.reply("🎤 Listening…")
    src = os.path.join(DL_DIR, f"v_{voice_msg.message_id}.ogg")
    fobj = voice_msg.voice or voice_msg.audio or voice_msg.video or voice_msg.video_note
    await bot.download(fobj, destination=src)
    wav = await to_wav(src)
    
    # 1. Try ACRCloud (humming engine if key provided)
    # 2. Try Shazam (studio track acoustic fingerprint)
    # 3. Try Lyrics / Human Voice Recognition (singing / spoken lyrics)
    q = await acr_identify(wav) or await shazam_identify(wav) or await lyrics_identify(wav)
    for f in (src, wav):
        try:
            os.remove(f)
        except OSError:
            pass
    if not q:
        return await status.edit_text("😕 Couldn't recognize it. Try humming longer (10–15s) and closer to the mic.")
    songs = await search(q)
    if not songs:
        return await status.edit_text(f"Found “{q}” but couldn't get the track.")
    await status.edit_text(f"🎯 Is it one of these?", reply_markup=results_keyboard(songs[:3]))
    if not cached(songs[0]["id"]):
        asyncio.create_task(get_file_id(bot, songs[0]))


# 1. Voice, audio or video sent in Private Chat (DM) -> Auto Recognize immediately!
@router.message(F.chat.type == "private", F.voice | F.audio | F.video | F.video_note)
async def auto_recognize_dm(m: Message, bot: Bot):
    await recognize(m, bot, m)


# 2. Voice, audio or video with caption 'find' / 'shazam' in groups or DMs
@router.message((F.voice | F.audio | F.video | F.video_note) & F.caption.regexp(r"(?i)^/?(find|shazam|song|whatsong)\b"))
async def find_from_caption(m: Message, bot: Bot):
    await recognize(m, bot, m)


# 3. Reply 'find' / 'shazam' to voice, audio or video in groups
@router.message(F.text.regexp(r"(?i)^/?(find|shazam|song|whatsong)\b"), F.reply_to_message)
async def find_from_reply(m: Message, bot: Bot):
    r = m.reply_to_message
    if r.voice or r.audio or r.video or r.video_note:
        await recognize(m, bot, r)
    else:
        await m.reply("Reply to a voice note, audio or video with <code>find</code>", parse_mode="HTML")


# ───────────────────────────── run ─────────────────────────────
async def main():
    bot = Bot(BOT_TOKEN)
    me = await bot.get_me()
    print("=" * 60, flush=True)
    print(f"[✓] Teens Music Bot Online: @{me.username} ({me.first_name})", flush=True)
    print(f"[✓] Storage Channel: {STORAGE_CHAT_ID}", flush=True)
    print(f"[✓] Live Suggestions, Instant Delivery & Voice ID Active!", flush=True)
    print("=" * 60, flush=True)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"])


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except Exception as e:
            print(f"\n[!] Music Bot exception: {e}. Auto-restarting in 3s...", flush=True)
            time.sleep(3)
