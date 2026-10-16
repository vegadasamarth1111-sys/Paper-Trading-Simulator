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
│   ├── broker/              # Phase 2: costs, fills, margin, ledger, EOD
│   │   └── management/commands/  # load_cost_table, verify_costs, reconcile, settle_day
│   ├── risk/                # Phase 3: 12-rule engine, kill switch, limit seeding
│   │   └── management/commands/  # seed_risk_defaults, kill_switch
│   ├── engine/              # Phase 4: strategies, sizing, backtest/replay runner
│   │   ├── strategies/      # sma_cross + donchian baselines + registry
│   │   └── management/commands/  # backtest, replay
│   ├── tests/               # 577 pytest tests (41 engine + 28 risk + 113 broker + 73 market data)
│   └── requirements/        # base.txt / prod.txt / dev.txt (pinned)
├── docker/                  # Dockerfiles
├── .github/workflows/ci.yml # CI: lint, bandit, pip-audit, secrets, tests, deploy checks
├── pyproject.toml           # ruff / mypy / bandit / pytest / coverage config
└── engine/  frontend/       # Phase 5+ (not yet created)
```

## Paper Broker (Phase 2 — ₹10L paper, Zerodha-style costs, no naked shorts)

```bash
cd backend
python manage.py load_cost_table   # seed the versioned charge schedule
python manage.py verify_costs      # 20-trade table: tick off vs broker's calculator
python manage.py reconcile         # rebuild balances/positions from fills, report drift
python manage.py settle_day        # expire DAY orders, square off MIS, settle, snapshot
```

Fill realism (plan §5.3): market fills at bid/ask (never LTP), conservative
limits need trade-through, stops trigger on LTP cross, one tick absorbs at
most 50% of displayed depth, circuits halt, illiquid names reject, 2bps +
300ms-latency defaults — all in `broker/fills.py`, all unit-tested.

## Risk Engine (Phase 3 — 12 rules + kill switch)

```bash
cd backend
python manage.py seed_risk_defaults --account <UUID>   # documented limits, never overwrites tuned rows
python manage.py kill_switch --account <UUID> --reason "manual halt"
python manage.py kill_switch --account <UUID> --release --reason "false alarm"
```

All plan §6 rules enforced between strategy and broker (first breach wins,
every rejection logged with its rule): position size (₹2L + 20%), 10 open
positions, 1% risk/trade from stop distance, 2% daily loss, 10% drawdown
halt, 50 trades/day, mandatory stops, duplicate-signal guard, 10/min order
rate, trading window (no entries before 09:20 or after 15:15 IST), kill
switch, stale-tick guard. Exits are never trapped — only the kill switch
blocks exits, and kill/EOD square-offs bypass with full audit.

## Strategy Engine (Phase 4 — baselines + deterministic replay)

```bash
cd backend
python manage.py backtest --account <UUID> --strategy sma_cross --symbols RELIANCE \
  --start 2026-01-05T00:00:00+00:00 --end 2026-01-06T00:00:00+00:00
python manage.py replay --account <UUID> --strategy donchian --symbols RELIANCE \
  --start 2026-01-05T00:00:00+00:00 --end 2026-01-06T00:00:00+00:00
```

Strategies emit signals, never orders (`sma_cross`, `donchian` baselines).
Sizing risks 1% of allocation per stop distance, rounded to lots; the same
broker + risk code runs backtest (closed candles), replay (recorded ticks),
and live paper — replaying a day twice gives byte-identical financials
(the exit-gate test).

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
| Tests + coverage | `pytest` (coverage floor 80%, currently 91%) |
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
| 1. Data Layer | ✅ Done — ticks/candles/providers/ingestion/quality on free tier (live 5-day proof pending) |
| 2. Paper Broker Core | ✅ Done — fills, costs, margin, ledger, EOD (broker-calculator check is yours) |
| 3. Risk Engine | ✅ Done — 12 rules + kill switch, every rule has a blocking test |
| 4. Strategy Framework + Replay | 🔨 In Progress — baselines, sizing, deterministic runner built; needs your review |
| 2. Paper Broker Core | ⏳ Planned |
| 3. Risk Engine | ⏳ Planned |
| 4. Strategy Framework | ⏳ Planned |
| 5. API & Dashboard | ⏳ Planned |
| 6. ML Integration | ⏳ Planned |
| 7. Hardening | ⏳ Planned |
| 8. Live Paper Trial | ⏳ Planned |

## License

Private — All rights reserved.
