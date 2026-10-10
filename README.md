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
│   ├── tests/               # 320 pytest tests
│   └── requirements/        # base.txt / prod.txt / dev.txt (pinned)
├── docker/                  # Dockerfiles
├── .github/workflows/ci.yml # CI: lint, bandit, pip-audit, secrets, tests, deploy checks
├── pyproject.toml           # ruff / mypy / bandit / pytest / coverage config
└── engine/  frontend/       # Phase 2+ (not yet created)
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
| Tests + coverage | `pytest` (coverage floor 80%, currently 98%) |
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
| 1. Data Layer | ⏳ Planned |
| 2. Paper Broker Core | ⏳ Planned |
| 3. Risk Engine | ⏳ Planned |
| 4. Strategy Framework | ⏳ Planned |
| 5. API & Dashboard | ⏳ Planned |
| 6. ML Integration | ⏳ Planned |
| 7. Hardening | ⏳ Planned |
| 8. Live Paper Trial | ⏳ Planned |

## License

Private — All rights reserved.
