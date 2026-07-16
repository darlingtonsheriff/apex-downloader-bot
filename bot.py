"""
Personal Video Downloader Telegram Bot
---------------------------------------
Send it a TikTok, Instagram, or YouTube link and it replies with the video
(and for Instagram carousels, every photo/video in the post).
No forced group-joins, no ads, no gatekeeping — just the file(s).

SETUP:
1. Get a bot token from @BotFather on Telegram (send /newbot, follow prompts).
2. Put that token in the BOT_TOKEN environment variable (see README.md).
3. Install dependencies:  pip install -r requirements.txt
4. Run:  python bot.py

The bot uses yt-dlp under the hood, which supports TikTok, Instagram, and
YouTube (and dozens of other sites) out of the box.
"""

import os
import re
import logging
import tempfile
import asyncio

import yt_dlp
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Optional: your own Telegram numeric user ID. If set, the bot will DM you
# after several downloads in a row fail — usually a sign yt-dlp needs updating.
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID")
FAILURE_ALERT_THRESHOLD = 3
_consecutive_failures = 0

# Telegram bots can send files up to 50MB via the standard Bot API.
MAX_FILE_SIZE_MB = 50

# Cap how many downloads run at once. Extra requests wait in line instead of
# firing all together, which avoids getting rate-limited by TikTok/Instagram
# and keeps each individual download/upload from crawling due to split bandwidth.
MAX_CONCURRENT_DOWNLOADS = 3
_download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# Optional: paste the full contents of a cookies.txt file (covering any sites that
# need login — YouTube's bot-check, Instagram stories/private posts, etc.) into
# this environment variable when running on a host (e.g. Railway) where you can't
# drop an actual cookies.txt file next to the script. COOKIES_TXT is the preferred
# name; YOUTUBE_COOKIES is kept as a fallback for anyone who already set that one.
COOKIES_ENV = os.environ.get("COOKIES_TXT") or os.environ.get("YOUTUBE_COOKIES")

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic"}
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "webm"}

URL_PATTERN = re.compile(
    r"(https?://(?:www\.)?(?:"
    r"tiktok\.com|vt\.tiktok\.com|"
    r"instagram\.com|"
    r"youtube\.com|youtu\.be"
    r")/\S+)",
    re.IGNORECASE,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a TikTok, Instagram, or YouTube link and I'll send the video(s) back.\n"
        "No group-joins, no ads — just the file(s)."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _consecutive_failures
    text = update.message.text or ""
    match = URL_PATTERN.search(text)

    if not match:
        await update.message.reply_text(
            "That doesn't look like a TikTok, Instagram, or YouTube link. Send me one of those."
        )
        return

    url = match.group(1)
    chat_id = update.effective_chat.id
    # Instagram carousels and TikTok "photo mode" slideshows can contain multiple
    # photos/videos in one post — both need noplaylist=False to grab every item.
    # YouTube stays noplaylist=True so a playlist link doesn't download the whole thing.
    is_multi_item_site = "instagram.com" in url.lower() or "tiktok.com" in url.lower()

    # React with 👀 on the user's own message
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=update.message.message_id,
            reaction="👀",
        )
    except Exception:
        logger.exception("Could not set message reaction")

    queued = _download_semaphore.locked()
    status_msg = await update.message.reply_text(
        "Queued — other downloads are ahead of yours ⏳" if queued else "Downloading… ⏳"
    )

    async with _download_semaphore:
        if queued:
            await status_msg.edit_text("Downloading… ⏳")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_template = os.path.join(tmp_dir, "%(id)s_%(autonumber)s.%(ext)s")
            ydl_opts = {
                "outtmpl": out_template,
                # bestvideo+bestaudio guarantees an audio track always gets paired
                # with the video. We then force a re-encode below to standard
                # H.264/AAC regardless of the source codec, so playback (and audio)
                # is guaranteed on every device.
                "format": "bestvideo+bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                # Instagram carousels and TikTok slideshows (multi-photo/video posts)
                # need noplaylist=False to get every item. Keep it True elsewhere so a
                # YouTube playlist link doesn't accidentally grab the whole playlist.
                "noplaylist": not is_multi_item_site,
                "merge_output_format": "mp4",
                # Force re-encode to H.264 video + AAC audio, and put playback
                # metadata at the START of the file (faststart) so it doesn't
                # freeze on the first frame while streaming/previewing.
                "postprocessor_args": {
                    "default": ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"]
                },
            }

            # Cookies for sites that require login verification (YouTube bot-check,
            # Instagram stories/private content). Prefer a local cookies.txt next to
            # the script; fall back to the YOUTUBE_COOKIES environment variable
            # (used on hosts like Railway where you can't drop a plain file next to
            # the code).
            cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
            if os.path.exists(cookies_path):
                ydl_opts["cookiefile"] = cookies_path
            elif COOKIES_ENV:
                env_cookies_path = os.path.join(tmp_dir, "cookies.txt")
                with open(env_cookies_path, "w", encoding="utf-8") as cf:
                    cf.write(COOKIES_ENV)
                ydl_opts["cookiefile"] = env_cookies_path

            try:
                loop = asyncio.get_event_loop()

                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        entries = info.get("entries") if info.get("entries") is not None else [info]
                        results = []
                        for entry in entries:
                            if not entry:
                                continue
                            fp = ydl.prepare_filename(entry)
                            results.append((fp, entry))
                        return results

                results = await loop.run_in_executor(None, download)

                if not results:
                    await status_msg.edit_text("Couldn't find anything downloadable in that link.")
                    return

                sent_videos = 0
                sent_photos = 0
                skipped_too_big = 0

                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)

                for filepath, entry in results:
                    # yt-dlp may merge/convert to .mp4 even if prepare_filename guessed differently
                    if not os.path.exists(filepath):
                        base, _ext = os.path.splitext(filepath)
                        for alt_ext in (".mp4", ".jpg", ".jpeg", ".png", ".webp"):
                            candidate = base + alt_ext
                            if os.path.exists(candidate):
                                filepath = candidate
                                break

                    if not os.path.exists(filepath):
                        continue

                    ext = os.path.splitext(filepath)[1].lstrip(".").lower()

                    if ext in VIDEO_EXTENSIONS:
                        size_mb = os.path.getsize(filepath) / (1024 * 1024)
                        if size_mb > MAX_FILE_SIZE_MB:
                            skipped_too_big += 1
                            continue
                        with open(filepath, "rb") as f:
                            await update.message.reply_video(
                                video=f,
                                supports_streaming=True,
                                width=entry.get("width"),
                                height=entry.get("height"),
                                duration=entry.get("duration"),
                            )
                        sent_videos += 1

                    elif ext in IMAGE_EXTENSIONS:
                        with open(filepath, "rb") as f:
                            await update.message.reply_photo(photo=f)
                        sent_photos += 1

                summary_parts = []
                if sent_videos:
                    summary_parts.append(f"{sent_videos} video(s)")
                if sent_photos:
                    summary_parts.append(f"{sent_photos} photo(s)")
                if skipped_too_big:
                    summary_parts.append(f"{skipped_too_big} skipped (over {MAX_FILE_SIZE_MB}MB)")

                if sent_videos or sent_photos:
                    await status_msg.delete()
                    try:
                        await context.bot.set_message_reaction(
                            chat_id=chat_id,
                            message_id=update.message.message_id,
                            reaction="✅",
                        )
                    except Exception:
                        logger.exception("Could not update message reaction")
                    _consecutive_failures = 0
                else:
                    await status_msg.edit_text(
                        "Couldn't send anything from that link "
                        f"({', '.join(summary_parts) if summary_parts else 'nothing downloadable'})."
                    )

            except Exception as e:
                logger.exception("Download failed for %s", url)
                await status_msg.edit_text(f"Couldn't download that one. ({str(e)[:200]})")

                _consecutive_failures += 1
                if OWNER_CHAT_ID and _consecutive_failures >= FAILURE_ALERT_THRESHOLD:
                    try:
                        await context.bot.send_message(
                            chat_id=OWNER_CHAT_ID,
                            text=(
                                f"⚠️ {_consecutive_failures} downloads have failed in a row.\n"
                                "Likely fix: redeploy to pick up the latest yt-dlp, "
                                "or refresh cookies.txt if it's a YouTube/Instagram-login link."
                            ),
                        )
                    except Exception:
                        logger.exception("Could not send owner alert")


def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is not set. See README.md.")

    # Workaround for a known python-telegram-bot bug on Python 3.14+:
    # the library expects asyncio.get_event_loop() to auto-create a loop,
    # but 3.14 removed that behavior. So we create and set one ourselves first.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(120)
        .write_timeout(120)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
