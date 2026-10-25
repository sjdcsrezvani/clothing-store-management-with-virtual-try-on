"""Developer-only tool for loading an existing database into a desktop install.

The packaged app starts empty and runs the setup wizard. This script is for the
developer (or a migration technician) who already has a store database and
wants to place it in the app's data directory instead of retyping the setup.

    python provision_data.py --source-db referral.db --source-env .env --with-uploads

The target contains real data and secrets. Keep it private; never commit it or
ship it inside a customer build.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from pathlib import Path

from deployment import APP_SLUG

PROJECT_ROOT = Path(__file__).resolve().parent


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    """Copy a consistent SQLite snapshot, including any live WAL changes."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_suffix(destination.suffix + ".tmp")
    temp_destination.unlink(missing_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        destination_connection = sqlite3.connect(temp_destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()
    os.replace(temp_destination, destination)
    # Never carry stale sidecar files into the provisioned target.
    Path(str(destination) + "-wal").unlink(missing_ok=True)
    Path(str(destination) + "-shm").unlink(missing_ok=True)


def _copy_uploads(source_uploads: Path, target: Path) -> None:
    """Copy product/barcode/invoice images into the target data dir.

    The target's static dir must be a real directory (never a symlink into
    an app bundle), otherwise the copy would follow the symlink and write
    inside the bundle.
    """
    target_static = target / "static"
    if target_static.is_symlink():
        target_static.unlink()
    target_static.mkdir(parents=True, exist_ok=True)
    target_uploads = target_static / "uploads"
    target_uploads.mkdir(parents=True, exist_ok=True)
    if not source_uploads.is_dir():
        print(f"No uploads dir at {source_uploads}; skipping image copy.")
        return
    shutil.copytree(
        source_uploads,
        target_uploads,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("temp"),
    )
    print(f"Copied uploads from {source_uploads} (temp/ excluded).")


def provision(
    source_db: Path,
    source_env: Path,
    target: Path,
    source_uploads: Path | None = None,
) -> None:
    if not source_db.is_file():
        raise SystemExit(f"Source database not found: {source_db}")
    if not source_env.is_file():
        raise SystemExit(f"Source environment file not found: {source_env}")

    target.mkdir(parents=True, exist_ok=True)
    _copy_sqlite_database(source_db, target / "referral.db")
    shutil.copy2(source_env, target / ".env")
    if source_uploads is not None:
        _copy_uploads(source_uploads, target)
    (target / ".setup_done").write_text("provisioned\n", encoding="utf-8")
    print(f"Provisioned store data into: {target}")
    print("The target contains secrets; keep it private and do not commit it.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load an existing store database into a desktop data dir")
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument(
        "--with-uploads",
        action="store_true",
        help="Also copy static/uploads images into the target data dir",
    )
    parser.add_argument(
        "--source-uploads",
        type=Path,
        default=PROJECT_ROOT / "static" / "uploads",
    )
    args = parser.parse_args()

    if args.target is None:
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA", Path.home()))
        elif os.uname().sysname == "Darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path.home() / ".local" / "share"
        args.target = base / APP_SLUG

    provision(
        args.source_db,
        args.source_env,
        args.target,
        source_uploads=args.source_uploads if args.with_uploads else None,
    )


if __name__ == "__main__":
    main()
