# Paper Trading Simulator — Security review (Phase 7, 2026-10-16)

Point-in-time review. Re-run the commands after any dependency, settings,
or auth change. Anything marked FAIL blocks the release.

## 1. Automated gates (all in CI, all blocking)

| Check | Command | Current |
|---|---|---|
| Lint/format | `ruff check backend/`, `ruff format --check backend/` | PASS |
| Types | `mypy .` (django-stubs) | PASS, 0 issues |
| Static analysis | `bandit -r backend/ -c pyproject.toml` | PASS, 0 issues |
| Dependencies | `pip-audit -r backend/requirements/{base,prod,dev}.txt` | PASS, 0 CVEs |
| Secrets | `detect-secrets-hook --baseline .secrets.baseline` | PASS, 15 vetted fixture findings |
| Deploy checks | `manage.py check --deploy --fail-level WARNING` | PASS, 0 issues |
| Posture pins | `pytest tests/test_security_review.py` | PASS |

## 2. Authentication and sessions

- JWT is the ONLY auth (`DEFAULT_AUTHENTICATION_CLASSES` pinned in test).
- Access tokens 15 min, refresh rotation + blacklist on (pinned in test).
- Passwords: Argon2id first hasher in production settings (pinned via
  subprocess against `config.settings.base`; test runner deliberately
  uses MD5 for speed — pinned as the documented exception).
- Sessions: HttpOnly + Lax SameSite on session and CSRF cookies.
- Brute force: `AuthRateThrottle` 5/min on login/register (tested).

## 3. Transport and headers

- JSON only in production (no browsable API to leak stack traces).
- CORS whitelist-only (`CORS_ALLOW_ALL_ORIGINS = False`, pinned).
- CSP deny-by-default (`default-src 'self'`, `frame-src/object-src 'none'`).
- HSTS + secure cookies enforced in `production.py`
  (`check --deploy` fails the build otherwise).

## 4. Authorization

- Every trading endpoint resolves the account from `request.user`;
  cross-account rows 404 identically to missing rows (tested both
  directions, incl. orders/trades/positions/risk/strategies).
- Kill switch is permission-gated AND engine-gated (defense in depth,
  both layers tested).
- Admin routes behind IP-whitelist middleware (documented residual:
  `X-Forwarded-For` is trusted — only deploy behind a proxy you control
  that strips/forwards it correctly).

## 5. Input handling

- DRF serializer validation on every write path (types, ranges, required
  stop/trigger fields); malformed input is 400 with no stack traces
  (custom exception handler, tested).
- bleach HTML stripping on user content; SQL-injection request screen
  covers raw AND percent-decoded query strings (regression-tested).
- The `on\w+=` event-handler heuristic false-positives on params like
  `once=1` — endpoint params avoid the shape instead of weakening the
  control (pinned by the streaming test).

## 6. Money safety

- Decimal everywhere (float prices refused at the schema boundary).
- Fills are immutable; corrections are adjusting entries.
- Ledger is the balance truth; nightly `reconcile` rebuilds balance +
  positions + charges from fills (mismatch = critical alert, tested
  incl. a Hypothesis property over random fill sequences).
- Backups are Fernet-encrypted with manifest hashes; restore fails
  closed on tampering (tested: roundtrip, tamper, wrong key, no key).

## 7. Known residual risks (honest list, not hidden)

1. Live/paper guard depends on operators setting `TRADING_MODE`
   correctly (refused at boot on contradiction, but review the env).
2. Yahoo data is ~15-min delayed and unofficial — paper-grade only.
3. Single-user threat model for the dashboard token storage
   (localStorage); httpOnly cookies before any multi-user deployment.
4. `ADMIN_IP_WHITELIST` trusts `X-Forwarded-For` (see §4).
5. Backups live wherever `BACKUP_DIR` points — replicate off-host
   (the encrypt-then-copy step is operational, monthly drill in RUNBOOK).

## 8. How to re-run this review

```bash
cd backend
pytest tests/test_security_review.py tests/test_security_auth.py \
  tests/test_security_middleware.py tests/test_security_permissions.py -q
../backend/venv/Scripts/python.exe manage.py check --deploy --fail-level WARNING \
  --settings=config.settings.production
pip-audit -r requirements/base.txt -r requirements/prod.txt -r requirements/dev.txt
```
