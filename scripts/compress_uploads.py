#!/usr/bin/env python3
"""Compress stored person and garment photos. Dry-run by default.

Only the two storage folders the try-on flow owns are touched. Filenames and
extensions stay unchanged, so database image paths keep working.
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.image_storage import compress_for_storage

TARGETS = (
    ROOT / "static/uploads/products",
    ROOT / "static/uploads/tryon/people",
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def iter_images():
    for folder in TARGETS:
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                yield path


def compress_file(path: Path, *, apply: bool = False, backup: Path | None = None) -> tuple[int, int]:
    """Return (before_bytes, after_bytes) for one file."""
    raw = path.read_bytes()
    data, suffix = compress_for_storage(raw, path.suffix)
    if suffix != path.suffix.lower():
        # A format change would break the stored DB path; keep the original.
        return len(raw), len(raw)
    # Below 10% there is no real win, and re-encoding a >1MB 1600px copy on
    # the next run would only lose quality. Keeps a second run a no-op.
    if len(data) >= len(raw) * 0.9:
        return len(raw), len(raw)
    if apply:
        if backup is not None:
            try:
                relative = path.relative_to(ROOT)
            except ValueError:
                relative = Path(path.name)
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(path, destination)
        stat = path.stat()
        fd, temp_name = tempfile.mkstemp(
            prefix=f"{path.stem}-", suffix=path.suffix, dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(temp_name, stat.st_mode)
            os.replace(temp_name, path)
            # The 30-day retention clock follows the original upload date.
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return len(raw), len(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the compressed files (default: dry-run)")
    parser.add_argument("--backup", type=Path, default=None,
                        help="copy originals here before replacing (use with --apply)")
    args = parser.parse_args()

    before = after = 0
    changed = 0
    for path in iter_images():
        raw_size, new_size = compress_file(path, apply=args.apply, backup=args.backup)
        before += raw_size
        after += new_size
        if new_size < raw_size:
            changed += 1
            print(f"{path.relative_to(ROOT)}: {raw_size / 1e6:.2f} MB -> {new_size / 1e6:.2f} MB")

    saved = before - after
    percent = (saved / before * 100) if before else 0
    mode = "applied" if args.apply else "dry-run"
    print(f"\n{mode}: {changed} files, {before / 1e6:.2f} MB -> {after / 1e6:.2f} MB "
          f"({percent:.1f}% saved)")
    if not args.apply:
        print("Run again with --apply to write the changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
