# Paper Trading Simulator

A production-grade paper trading platform for Indian markets (NSE/BSE) that simulates real broker behavior with virtual capital.

## Architecture

- **Backend**: Python 3.14, Django 5.2 LTS, Django REST Framework
- **Trading Engine**: Standalone asyncio service (not inside Django)
- **Database**: MySQL 8 (InnoDB), money always `Decimal` (never float)
- **Cache / Event Bus**: Redis Streams
- **Task Queue**: Celery + Celery Beat
- **Frontend**: React (Phase 5)
- **Monitoring**: Prometheus + Grafana, Sentry
- **Alerts**: Telegram Bot API

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.14+
- Node.js 18+ (for frontend, Phase 5)

### Setup

```bash
# Clone
git clone https://github.com/vegadasamarth1111-sys/Paper-Trading-Simulator.git
cd Paper-Trading-Simulator

# Create env file from template — fill in your values
cp .env.example .env

# Start infrastructure (MySQL, Redis)
docker-compose up -d db redis

# Backend setup
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements/dev.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Docker (Full Stack)
```bash
docker-compose up --build
```

## Project Structure

```
├── backend/                 # Django API + domain models
│   ├── config/              # Settings (base / development / production / testing)
│   ├── core/                # Domain models, clock, enums, types, migrations
│   ├── security/            # Auth, permissions, middleware, throttles
│   ├── instruments/         # Instrument master loader + services
│   ├── market_data/         # Phase 1: ticks, candles, providers, ingestion, quality
│   │   ├── providers/       # yahoo (keyless) + angel (keyless master, keyed quotes)
│   │   └── management/commands/  # poll_prices, backfill_history, sync_master, data_quality_report
│   ├── tests/               # 393 pytest tests (73 for market data)
│   └── requirements/        # base.txt / prod.txt / dev.txt (pinned)
├── docker/                  # Dockerfiles
├── .github/workflows/ci.yml # CI: lint, bandit, pip-audit, secrets, tests, deploy checks
├── pyproject.toml           # ruff / mypy / bandit / pytest / coverage config
└── engine/  frontend/       # Phase 2+ (not yet created)
```

## Market Data (Phase 1 — free tier, zero keys)

No paid Kite Connect. No keys in `.env` required today:

| Provider | Keys? | What it gives |
|----------|-------|---------------|
| **Yahoo Finance** (default, `MARKET_DATA_PROVIDER=yahoo`) | None — keyless | 1m/5m/15m/1h/1d history + 1-min LTP polling for NIFTY 50 + NIFTY/BANKNIFTY/INDIAVIX (~15-min delayed) |
| **Angel One** (`MARKET_DATA_PROVIDER=angel`) | Master: none — public ScripMaster JSON. Quotes: `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET` (you have a free API; add keys when ready, code is already wired) | Real exchange tokens, lot/tick sizes, expiries; real-time quotes + websocket once keys exist |

Live paper runs on **1-min polling** (true tick websocket needs broker login).
Every poll writes the raw tick first (replay fidelity), validates against all
9 rules in plan §4.4 (rejects → `bad_ticks` with reason codes), folds ticks
into 1m candles (09:15-IST grid, explicit empty-candle markers, closed-only
reads), caches latest prices in Redis, and watches a heartbeat for silence.

```bash
cd backend
python manage.py sync_master                        # master from Yahoo seed / Angel
python manage.py backfill_history --symbols RELIANCE,TCS --interval 1d --days 30
python manage.py poll_prices --once                  # single cycle (Beat uses this)
python manage.py poll_prices                         # forever loop (supervisor/dev)
python manage.py data_quality_report                 # after-close verdict (OK/SUSPECT)
```

## Quality Gates

Every one of these runs in CI and **blocks the merge on failure** — there are no
`|| true` escapes:

| Gate | Tool |
|------|------|
| Lint | `ruff check backend/` |
| Format | `ruff format --check backend/` |
| Static security | `bandit -r backend/ -c pyproject.toml` |
| Dependency CVEs | `pip-audit` on `base.txt`, `prod.txt`, `dev.txt` |
| Leaked secrets | `detect-secrets-hook` against `.secrets.baseline` |
| Tests + coverage | `pytest` (coverage floor 80%, currently 90%) |
| Migration drift | `manage.py makemigrations --check --dry-run` |
| Deploy readiness | `manage.py check --deploy --fail-level WARNING` |
| Types | `mypy` with django-stubs |

Run the same checks locally before pushing:

```bash
cd backend
pip install -r requirements/dev.txt
ruff check . && ruff format --check .
bandit -r . -c ../pyproject.toml -x venv,tests
mypy .
pytest
```

Or install them as a git hook so the same checks run on every `git commit`:

```bash
pre-commit install
```

## Security

This project treats security as a first-class concern:

- **Secrets**: All API keys and credentials live in environment variables, never in code
- **Authentication**: JWT with short-lived access tokens, rotation and blacklisting on refresh
- **Authorization**: Object-level permission checks — a user can only reach their own accounts
- **Password Hashing**: Argon2id (memory-hard, resistant to GPU attacks)
- **Rate Limiting**: Per-endpoint throttling on all API routes
- **Headers**: CSP (via django-csp), HSTS, X-Content-Type-Options, X-Frame-Options
- **Input Sanitization**: DRF serializer validation, bleach HTML stripping, SQL-injection request screening
- **Admin Protection**: IP whitelist middleware on admin routes
- **Debug**: `DEBUG` is False outside development; production settings refuse to boot if `DJANGO_DEBUG=True`
- **Secret Scanning**: `detect-secrets` baseline + hook blocks accidental commits
- **Dependency Auditing**: `pip-audit` runs in CI on every commit
- **Environment Guard**: Explicit `TRADING_MODE` flag prevents live keys in paper mode

## Development Phases

| Phase | Status |
|-------|--------|
| 0. Foundations | ✅ Done — repo, Docker, CI, settings, Clock, models, instrument loader |
| 1. Data Layer | 🔨 In Progress — ticks/candles/providers/ingestion/quality built on free tier (Yahoo + Angel), needs live 5-day no-gap proof |
| 2. Paper Broker Core | ⏳ Planned |
| 3. Risk Engine | ⏳ Planned |
| 4. Strategy Framework | ⏳ Planned |
| 5. API & Dashboard | ⏳ Planned |
| 6. ML Integration | ⏳ Planned |
| 7. Hardening | ⏳ Planned |
| 8. Live Paper Trial | ⏳ Planned |

## License

Private — All rights reserved.
