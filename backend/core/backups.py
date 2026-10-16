"""
Paper Trading Simulator — Encrypted database backups (Phase 7)
=================================================================
Daily encrypted backups with a TESTED restore (plan section 12).

Pipeline: dumpdata (JSON, engine-agnostic: identical for SQLite and
MySQL, no mysqldump binary needed) -> gzip -> Fernet/AES encrypt ->
ciphertext file + JSON manifest (SHA-256 of the ciphertext, timestamp,
engine, object counts).

Refusals (loud, by design):
- No BACKUP_ENCRYPTION_KEY: refuse to write plaintext. Backups without
  encryption violate the plan; generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
- Restore verifies the manifest hash BEFORE decrypting: a tampered file
  fails closed instead of loading garbage into the database.
"""

import gzip
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("core")
UTC = ZoneInfo("UTC")

MANIFEST_SUFFIX = ".manifest.json"


def _fernet(key: str):
    """Fernet instance or a clear refusal when no key is configured."""
    if not key:
        raise ValueError(
            "Refusing unencrypted backup: set BACKUP_ENCRYPTION_KEY "
            '(generate with: python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())")'
        )
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as exc:
        raise ValueError(f"BACKUP_ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def dump_plaintext() -> bytes:
    """Full-database JSON dump (excludes volatile session/cache tables)."""
    import io

    from django.core.management import call_command

    buffer = io.StringIO()
    call_command(
        "dumpdata",
        "--exclude",
        "sessions",
        "--exclude",
        "admin.logentry",
        "--exclude",
        "contenttypes",
        "--exclude",
        "auth.permission",
        stdout=buffer,
        verbosity=0,
    )
    return buffer.getvalue().encode("utf-8")


def create_backup(*, key: str, directory: str | Path) -> dict:
    """
    Write an encrypted backup + manifest. Returns
    {"path": ..., "sha256": ..., "bytes": ..., "objects": ...}.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    plaintext = dump_plaintext()
    objects = len(json.loads(plaintext.decode("utf-8")))
    compressed = gzip.compress(plaintext, compresslevel=9)
    ciphertext = _fernet(key).encrypt(compressed)
    digest = hashlib.sha256(ciphertext).hexdigest()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = directory / f"backup-{stamp}.json.gz.fernet"
    path.write_bytes(ciphertext)
    manifest = {
        "path": path.name,
        "sha256": digest,
        "bytes": len(ciphertext),
        "objects": objects,
        "created": datetime.now(UTC).isoformat(),
        "engine": _engine_name(),
    }
    Path(str(path) + MANIFEST_SUFFIX).write_text(json.dumps(manifest, indent=2))
    logger.info("Backup written: %s (%d objects, %d bytes)", path.name, objects, len(ciphertext))
    return {"path": str(path), "sha256": digest, "bytes": len(ciphertext), "objects": objects}


def restore_backup(*, key: str, path: str | Path) -> dict:
    """
    Verify manifest hash, decrypt, and load. Returns {"objects": n}.

    The database is flushed first (all non-migrated state goes), then the
    dump loads inside one transaction per loaddata batch. A wrong key or
    a single flipped ciphertext byte fails BEFORE anything is written.
    """
    import io
    import tempfile

    from django.core import serializers
    from django.core.management import call_command

    path = Path(path)
    manifest_path = Path(str(path) + MANIFEST_SUFFIX)
    if not manifest_path.is_file():
        raise ValueError(f"manifest missing for {path.name}; refusing blind restore")
    manifest = json.loads(manifest_path.read_text())
    ciphertext = path.read_bytes()
    if hashlib.sha256(ciphertext).hexdigest() != manifest.get("sha256"):
        raise ValueError(f"hash mismatch for {path.name}: file was modified; refusing restore")
    try:
        compressed = _fernet(key).decrypt(ciphertext)
    except Exception as exc:
        raise ValueError(f"decrypt failed (wrong key or corrupt file): {exc}") from exc
    plaintext = gzip.decompress(compressed)
    objects = list(serializers.deserialize("json", io.BytesIO(plaintext).read().decode("utf-8")))
    call_command("flush", "--no-input", verbosity=0)
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as tmp:
        tmp.write(plaintext)
        tmp_path = tmp.name
    try:
        call_command("loaddata", tmp_path, verbosity=0)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    logger.info("Restored %d objects from %s", len(objects), path.name)
    return {"objects": len(objects)}


def _engine_name() -> str:
    from django.db import connection

    return connection.vendor
