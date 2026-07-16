"""
Personal Video Downloader Telegram Bot
---------------------------------------
Send it a TikTok, Instagram, or YouTube link and it replies with the video.
No forced group-joins, no ads, no gatekeeping — just the file.

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
# If you need bigger files, you'd need to run a local Bot API server (see README).
MAX_FILE_SIZE_MB = 50

# Cap how many downloads run at once. Extra requests wait in line instead of
# firing all together, which avoids getting rate-limited by TikTok/Instagram
# and keeps each individual download/upload from crawling due to split bandwidth.
MAX_CONCURRENT_DOWNLOADS = 3
_download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# Optional: paste the full contents of a cookies.txt file into this environment
# variable when running on a host (e.g. Railway) where you can't drop an actual
# cookies.txt file next to the script. Used as a fallback if no local file exists.
YOUTUBE_COOKIES_ENV = os.environ.get("YOUTUBE_COOKIES")

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
        "Send me a TikTok, Instagram, or YouTube link and I'll send the video back.\n"
        "No group-joins, no ads — just the file."
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

    # React with 👀 on the user's own message (like the animation in your screenshot)
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
            out_template = os.path.join(tmp_dir, "%(id)s.%(ext)s")
            ydl_opts = {
                "outtmpl": out_template,
                # bestvideo+bestaudio: download the best video-only and best audio-only
                # streams separately and merge them with ffmpeg. Falls back to a single
                # pre-merged "best" stream if separate streams aren't available.
                "format": "bestvideo+bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "merge_output_format": "mp4",
                # Puts playback metadata at the START of the file instead of the end.
                # Without this, some players (including Telegram's in-app preview)
                # can show a frozen first frame until the whole file finishes loading.
                "postprocessor_args": {"default": ["-movflags", "+faststart"]},
            }

            # Cookies for sites that require login verification (mainly YouTube).
            # Prefer a local cookies.txt next to the script; fall back to the
            # YOUTUBE_COOKIES environment variable (used on hosts like Railway
            # where you can't drop a plain file next to the code).
            cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
            if os.path.exists(cookies_path):
                ydl_opts["cookiefile"] = cookies_path
            elif YOUTUBE_COOKIES_ENV:
                env_cookies_path = os.path.join(tmp_dir, "cookies.txt")
                with open(env_cookies_path, "w", encoding="utf-8") as cf:
                    cf.write(YOUTUBE_COOKIES_ENV)
                ydl_opts["cookiefile"] = env_cookies_path

            try:
                loop = asyncio.get_event_loop()

                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        return ydl.prepare_filename(info), info

                filepath, info = await loop.run_in_executor(None, download)

                # yt-dlp may merge to .mp4 even if prepare_filename guessed differently
                if not os.path.exists(filepath):
                    base, _ = os.path.splitext(filepath)
                    candidate = base + ".mp4"
                    if os.path.exists(candidate):
                        filepath = candidate

                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                if size_mb > MAX_FILE_SIZE_MB:
                    await status_msg.edit_text(
                        f"Video is {size_mb:.0f}MB, which is over Telegram's {MAX_FILE_SIZE_MB}MB "
                        "bot upload limit. Try a shorter/lower-quality link."
                    )
                    return

                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
                with open(filepath, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        supports_streaming=True,
                        width=info.get("width"),
                        height=info.get("height"),
                        duration=info.get("duration"),
                    )

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
                                "or refresh cookies.txt if it's a YouTube link."
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
