"""Developer-only owner-build provisioning tool.

Customers never provision anything and never see a setup wizard: the
developer prepares the store data at the code/build level with this script.

Two workflows:

1. Provision this machine's data dir (the packaged app reads it from there):

    python provision_owner.py --source-db referral.db --source-env .env --with-uploads

2. Stage an embeddable bundle so the NEXT packaged build self-seeds on any
   machine — double-click and the store is simply there:

    python provision_owner.py --source-db referral.db --source-env .env --stage

The staged bundle and the provisioned data dir contain the owner's real data
and secrets. Never commit them and never ship them inside a demo build.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from pathlib import Path

from deployment import OWNER_MODE

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
    if not OWNER_MODE:
        raise SystemExit(
            "deployment.py is not in OWNER_MODE; refusing to provision an owner build."
        )
    if not source_db.is_file():
        raise SystemExit(f"Source database not found: {source_db}")
    if not source_env.is_file():
        raise SystemExit(f"Source environment file not found: {source_env}")

    target.mkdir(parents=True, exist_ok=True)
    _copy_sqlite_database(source_db, target / "referral.db")
    shutil.copy2(source_env, target / ".env")
    if source_uploads is not None:
        _copy_uploads(source_uploads, target)
    (target / ".setup_done").write_text("owner-provisioned\n", encoding="utf-8")
    print(f"Provisioned owner data into: {target}")
    print("The target contains secrets; keep it private and do not commit it.")


def stage_bundle(source_db: Path, source_env: Path, stage_dir: Path) -> None:
    """Stage the owner bundle that gets embedded into the next packaged build.

    The staged app then starts with this store on any machine — no setup
    wizard, no customer-facing configuration. The bundle contains real
    secrets; it is gitignored and must never be shared.
    """
    if not OWNER_MODE:
        raise SystemExit(
            "deployment.py is not in OWNER_MODE; refusing to stage an owner bundle."
        )
    if not source_db.is_file():
        raise SystemExit(f"Source database not found: {source_db}")
    if not source_env.is_file():
        raise SystemExit(f"Source environment file not found: {source_env}")
    stage_dir.mkdir(parents=True, exist_ok=True)
    _copy_sqlite_database(source_db, stage_dir / "referral.db")
    shutil.copy2(source_env, stage_dir / ".env")
    print(f"Staged owner bundle: {stage_dir}")
    print("The next packaged build will start with this store automatically.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision or stage a private owner build")
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
    parser.add_argument(
        "--stage",
        action="store_true",
        help="Stage the embeddable owner_bundle/ for the next build instead of provisioning the data dir",
    )
    parser.add_argument("--stage-dir", type=Path, default=PROJECT_ROOT / "owner_bundle")
    args = parser.parse_args()

    if args.stage:
        stage_bundle(args.source_db, args.source_env, args.stage_dir)
        return

    if args.target is None:
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA", Path.home()))
        elif os.uname().sysname == "Darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path.home() / ".local" / "share"
        args.target = base / "RaykidStore"

    provision(
        args.source_db,
        args.source_env,
        args.target,
        source_uploads=args.source_uploads if args.with_uploads else None,
    )


if __name__ == "__main__":
    main()
