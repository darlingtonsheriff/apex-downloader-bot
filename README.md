# Personal Video Downloader Bot

Send it a TikTok, Instagram, or YouTube link. It sends the video back. No group-joins, no ads.

## 1. Create your bot (2 minutes)

1. Open Telegram, search for **@BotFather**.
2. Send `/newbot`, give it a name and a username (must end in "bot", e.g. `darlington_dl_bot`).
3. BotFather gives you a **token** — a string like `123456789:AAExampleTokenHere`. Copy it.

## 2. Run it locally (to test)

```bash
pip install -r requirements.txt
export BOT_TOKEN="paste_your_token_here"      # on Windows: set BOT_TOKEN=...
python bot.py
```

Now message your bot on Telegram with a link — it should reply with the video.
As long as `python bot.py` is running on your computer, the bot is live. Close the terminal and it goes offline — which is why for 24/7 use you want it on a server (next section).

## 2.5. (Optional) Get alerted when it breaks

Set an `OWNER_CHAT_ID` environment variable to your own numeric Telegram user ID, and the bot will DM you if 3 downloads fail in a row — usually means yt-dlp needs updating.

To find your numeric ID: message **@userinfobot** on Telegram, it replies with your ID instantly.

## 3. Keep it running 24/7 (pick one)

### Option A — Railway (easiest, free tier available)
1. Push this folder to a GitHub repo.
2. Go to railway.app → New Project → Deploy from GitHub repo.
3. In Railway's dashboard, add an environment variable: `BOT_TOKEN` = your token.
4. Set the start command to `python bot.py`. Railway auto-installs `requirements.txt`.

### Option B — Render
1. Push this folder to GitHub.
2. Render.com → New → Background Worker → connect the repo.
3. Build command: `pip install -r requirements.txt`. Start command: `python bot.py`.
4. Add `BOT_TOKEN` under Environment.

### Option C — Your own VPS (DigitalOcean, Linode, etc.)
```bash
git clone <your-repo-url>
cd tgbot
pip install -r requirements.txt
export BOT_TOKEN="your_token"
nohup python bot.py &          # keeps running after you disconnect
```
For something more robust, run it under `systemd` or `pm2` so it restarts automatically if it crashes or the server reboots.

## Notes & limits

- **File size**: standard Telegram bots can send files up to 50MB. Most short-form TikTok/Reels clips are well under that; long YouTube videos may hit the limit — the bot will tell you if a file's too big.
- **Instagram private/login-required content**: yt-dlp can only grab what's publicly viewable. Private accounts or login-walled posts won't download.
- **Platforms change their sites** occasionally, which can break extraction until yt-dlp releases an update. If downloads suddenly start failing, run `pip install -U yt-dlp` to get the latest fix — the yt-dlp project updates quickly when platforms change something.
- This is for personal use — respect creators' rights and each platform's terms when redistributing anything you download.
