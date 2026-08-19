# 🚀 OpenBudget Bot 1 - Railway Ready Repository (@opendvn_bot)

This repository contains the standalone source code for **OpenBudget Bot 1** (@opendvn_bot), pre-configured for **Railway.app** deployment with 1-click GitHub integration.

---

## 🌟 Key Features
- **Telegram Bot API 8.3 Native Styling**: Left buttons = Green (`style="success"`), Right buttons = Blue (`style="primary"`), Cancel button = Red (`style="danger"`).
- **32-Key Premium Animated Custom Emojis**: Full `<tg-emoji>` custom emoji manager configurable dynamically via Admin Panel.
- **FSM Cancellation**: Universal `Bekor qilish` cancellation at any step.
- **Payout Ticket System**: Card Holder Name verification, balance deduction/reservation, and instant admin/channel proof notifications.
- **High-Load Balancer Integration**: Automatic redirect link to 2-Bot (@opendvn2_bot) under high traffic.

---

## 🚂 Railway Deployment Guide

1. **Push to GitHub**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit for Bot 1"
   git remote add origin https://github.com/YOUR_USERNAME/openbudget-bot1.git
   git branch -M main
   git push -u origin main
   ```

2. **Deploy on Railway**:
   - Go to [railway.app](https://railway.app) and create a new project.
   - Click **Deploy from GitHub repo** and select `openbudget-bot1`.
   - Click **+ New** -> **Database** -> **Add PostgreSQL**.
   - Click **+ New** -> **Database** -> **Add Redis**.
   - In your Bot Service -> **Variables**, add:
     - `BOT_TOKEN`: `8709713103:AAFDufoeDTuo3R4VBQ3KgecniXM70x_kB38`
     - `ADMIN_TELEGRAM_IDS`: `6734269605,5916705324,8581373433`
     - `ADMIN_CHANNEL_ID`: `-5273763144`
     - `PAYOUT_PROOF_CHANNEL_ID`: `-1004487937644`
     - `PAYOUT_PROOF_CHANNEL_URL`: `https://t.me/+FFC_JlR5pR8xOWNi`
     - `SECONDARY_BOT_LINK`: `https://t.me/opendvn2_bot?start=redirect`
     - `OPENBUDGET_PROJECT_ID`: `board_123456`

Railway will automatically inject `DATABASE_URL` and `REDIS_URL` and launch your bot!
