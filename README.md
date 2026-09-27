# TaskFlow — Small Business Workflow & Task Management Platform
### Subtitle: a localized smart-retail workflow engine for micro-grocery ecosystems

Replaces scattered WhatsApp / notebook / spreadsheet task tracking with a simple digital workflow for small businesses — validated on a mom's grocery store.

Built for FITFEST 2026 Hackathon (Solo, ~4 hr MVP).

## Features
- Task creation (title, description, assignee, priority, deadline, status)
- Task assignment to team members
- Priority management (Low / Medium / High / Urgent)
- Status workflow (Todo / In Progress / Done)
- Dashboard: totals, completion %, overdue list, due-today, per-member productivity
- Overdue auto-detection (deadline < today & not Done)
- Team management (add / remove members)
- Search + filter by status / priority / member
- JSON API: `GET /api/tasks`

## Grocery retail focus (real-world validation)
- Stock + expiry tracker (`/inventory`): low-stock and ≤7-day expiry auto-create Urgent/High tasks
- Vendor procurement kanban (`/procurement`): PO Raised → Shipped → Received & Verified, auto follow-up tasks
- Order packing & fulfillment (`/orders`): picking checklists per order, status New → Picking → Packed → Delivered
- Daily retail checklists (`/checklists`): Morning Opening, Freezer Log, End-of-Day Cash templates
- WhatsApp ingestion: paste a customer message, parser extracts name/phone/items → order + packing task
- AI restock widget: top items from recent orders + low-stock suggestions on dashboard
- Offline-first: online/offline banner, form-draft persistence (localStorage), service worker (`/static/sw.js`)

## Revenue engine (growth)
- WhatsApp ordering workflow: text-a-list → structured order, no app needed
- Smart bundling (`/growth` + on `/orders`): e.g. tea → Sugar+Milk 10% off upsell pills
- Expiry flash sales: ≤2-day expiry auto-creates sale + broadcast message with copy button
- Khata + loyalty (`/customers`): credit ledger, visit tracking, 15-day inactive → retention tasks
- Group buying (`/growth`): society bulk deals, join flow, close → fulfilment task

## Tech Stack
- Backend: Flask 3 (Python)
- DB: SQLite (stdlib `sqlite3`, no ORM — zero-config, file at `workflow.db` locally / `/tmp/workflow.db` on Cloud Run)
- Frontend: Jinja templates + vanilla CSS (mobile responsive)
- Server: Gunicorn
- Deploy: Docker + Google Cloud Run

## Run locally
```bash
pip install -r requirements.txt
python app.py
# open http://localhost:8080
```

## Run with Docker
```bash
docker build -t taskflow .
docker run -p 8080:8080 -e PORT=8080 taskflow
```

## Deploy to Cloud Run
```bash
gcloud run deploy taskflow --source . --region asia-south1 --allow-unauthenticated
```
App listens on `$PORT` and serves `/healthz` for health checks.

## Project structure
```
app.py             # Flask app + SQLite init/seed + all routes
templates/         # base, dashboard, tasks, employees
static/style.css   # UI
requirements.txt
Dockerfile
```

## Demo login
Username `admin`, password `admin123` (seeded on first run).
```bash
# Cloud Run with a private session key
gcloud run deploy taskflow --source . --region asia-south1 --allow-unauthenticated --set-env-vars SECRET_KEY=$(openssl rand -hex 32)
```

## Install on Android (no Play Store needed)
TaskFlow is an installable web app (PWA) with offline support for the shop floor:
1. Open the Cloud Run link in **Chrome on your Android phone** and log in.
2. Tap **⋮ menu → Add to Home screen** (or **Install app** if shown).
3. Open **TaskFlow** from the home screen — it runs full-screen like a native app.

Why this works for the shop: the service worker caches pages and styles, so
checklists and tasks keep opening in backrooms with poor network.
