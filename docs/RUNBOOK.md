# Paper Trading Simulator — Runbook

What to do when something breaks. Every procedure below was exercised in
`backend/tests/test_chaos.py` (names in brackets) or on a live shell —
never trust a step you have not run.

Operations below assume: repo root shell, backend venv active
(`backend\venv\Scripts\activate` on Windows), `.env` present.

---

## 1. Market feed down / stale data

**Symptoms:** `data-health` shows `stale`/`unknown`, `poll_prices` logs
`provider fetch failed` with growing backoff, heartbeat errors naming
silent symbols, ticks landing in `bad_ticks` with `STALE_TS`.

**Do NOT trade into it.** The engine already blocks entries on stale
symbols (`STALE_DATA` rule); open positions are unaffected.

1. Check the provider from your laptop (Yahoo is a public website —
   open it in a browser; if it loads, the fault is local):
   `python backend\manage.py poll_prices --once --symbols RELIANCE`
2. If Yahoo is down: wait. Polling resumes on its own (exponential
   backoff to 5 min, then retries forever). No restart needed.
3. If only some symbols are silent: check `data_quality_report` for the
   day; a provider-side delisting shows as `missing_symbols`.
4. After recovery: confirm fresh ticks —
   `python backend\manage.py data_quality_report` should show the gap
   counted (gaps are REPORTED, never hidden) and the day marked SUSPECT
   if thresholds tripped. Suspect days are excluded from stats.

[Tests: `TestFeedDeath`, `TestStaleStorm`]

## 2. Database down / mid-fill crash

**Symptoms:** order placement raises, `reconcile` reports mismatches,
`trades` rows exist without matching ledger entries (should be
impossible — see below).

1. Every fill runs inside ONE transaction: a crash mid-fill rolls back
   the order, the trade, the ledger rows, and the position update
   together. There is no half-written fill by construction.
   [Test: `TestDbFailure::test_fill_abort_leaves_no_trace`]
2. On restart the engine rebuilds everything from the DB (brokers hold
   no in-memory positions; the ingestor reloads previous-tick context
   from the latest stored tick). Just restart the process — no replay,
   no manual repair. [Tests: `TestRestartMidday::*`]
3. Verify: `python backend\manage.py reconcile` must print
   `All accounts reconcile.` If it reports mismatches, treat it as a
   critical incident: freeze trading (kill switch), export the report,
   and investigate before placing another order.

## 3. Kill switch (halt everything)

1. Dashboard red button, or API: `POST /api/v1/kill-switch/ {"reason": "..."}`
2. Or CLI: `python backend\manage.py kill_switch --account <UUID> --reason "..."`
3. This cancels every resting order, squares every open position at
   market, and halts the account (new orders rejected at permission AND
   engine layers). Verify halted: `GET /api/v1/account/` shows
   `kill_switch_active: true`, and `positions/` is empty.
4. Release ONLY when the cause is understood and fixed:
   `python backend\manage.py kill_switch --account <UUID> --release --reason "..."`
   Release never happens on its own.

## 4. Backup and restore (tested monthly — calendar it)

1. Generate a key ONCE, store it in the secrets manager (never in git):
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Nightly (Celery Beat or cron, after settlement):
   `python backend\manage.py backup_db` (needs `BACKUP_ENCRYPTION_KEY`).
   Without a key the command REFUSES — plaintext backups are a finding.
3. Verify the manifest: `<file>.manifest.json` holds the SHA-256.
4. Restore (wipes current state — `--force` is deliberately required):
   `python backend\manage.py restore_db --file <path> --force`
   A tampered file or wrong key fails BEFORE anything is written.
5. Restore drill: back up dev, restore into a scratch database, run
   `reconcile`, confirm `All accounts reconcile.`
   [Tests: `TestDataProtectionPosture::test_backup_roundtrip*`]

## 5. Deploy rules

- **NEVER deploy 09:00–15:45 IST** on a trading day. Outside that window only.
- Deploy order: migrate first (`manage.py migrate`), then code, then
  `manage.py check --deploy --fail-level WARNING` (must print zero issues).
- Rollback: `git checkout <previous-sha>`, migrate is backwards-safe
  (migrations here only add tables/columns or relax nullability —
  verify with `makemigrations --check` before rolling forward again).
- Full-suite gate before every push: `ruff check . && ruff format --check .`,
  `mypy .`, `pytest`, `bandit -r . -c ../pyproject.toml -x venv,tests`,
  `pip-audit -r requirements/base.txt` (and prod/dev).

## 6. Load expectations (measured, Phase 7)

- Tick validation + 1m candle building: thousands/sec (pure Python).
- Full order lifecycle (risk + margin + ledger): ~40 orders/sec on SQLite.
- 53-symbol watchlist poll cycle: seconds, dominated by provider latency.
- Full NIFTY-50 peak-volatility replay procedure: backfill a volatile
  day's ticks, run `replay` with `--strategy sma_cross`, compare the
  data-quality report against the provider's official candles.
  [Tests: `test_load.py` (scaled, in CI)]

## 7. Contacts and secrets

- Secrets live in `.env` / the deploy host's secret store. The repo must
  never contain one: `detect-secrets-hook` blocks the commit otherwise.
- Telegram alerts need `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (free
  @BotFather bot). Unset = silent no-op; trading never depends on alerts.
