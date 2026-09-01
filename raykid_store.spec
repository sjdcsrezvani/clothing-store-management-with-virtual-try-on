# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Raykid Store desktop app.

Builds a standalone desktop app that bundles the FastAPI server, all static
assets, templates, and fonts, plus pywebview for the native window.

Usage:
    pyinstaller raykid_store.spec

On macOS this produces a .app bundle in dist/RaykidStore.app.
On Windows this produces dist/RaykidStore/RaykidStore.exe (onedir).

The app's writable data (DB, uploads, backups) lives in the user data dir,
not inside the bundle — see desktop_entry.py for the path logic.
"""

import os
import sys
from PyInstaller.utils.hooks import (
    collect_data_files, collect_submodules, copy_metadata,
)

# PyInstaller runs the spec with the project root as CWD.
PROJECT_ROOT = os.getcwd()

block_cipher = None

# ── Hidden imports that PyInstaller can't detect automatically ──────────────
# These are dynamically imported by FastAPI/uvicorn/SQLAlchemy and need to be
# listed explicitly or the frozen app will crash at runtime.
hiddenimports = [
    # FastAPI / uvicorn / Starlette internals
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "starlette.routing",
    "starlette.middleware.sessions",
    # SQLAlchemy dialect (SQLite)
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.dialects.sqlite.pysqlite",
    # Jinja2 / template rendering
    "jinja2.ext",
    "jinja2._identifier",
    # Our own modules (PyInstaller may miss these due to dynamic routing)
    "routers",
    "routers.admin",
    "routers.api",
    "routers.customers",
    "routers.products",
    "routers.sales",
    "routers.analytics",
    "routers.campaigns",
    "routers.accounting",
    "routers.clothes_images",
    "services",
    "services._common",
    "services.accounting",
    "services.analytics",
    "services.backup",
    "services.barcode",
    "services.discount",
    "services.image_gen",
    "services.invoice",
    "services.scheduler",
    "services.security",
    "services.sms",
    "services.store",
    "services.templating",
    "services.tier",
    "config",
    "database",
    "models",
    "main",
    # jdatetime / arabic-reshaper / python-bidi (runtime imports)
    "jdatetime",
    "arabic_reshaper",
    "bidi",
    "bidi.algorithm",
    # pywebview platform backends
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.cocoa",
    "webview.platforms.gtk",
    # Pillow / reportlab / barcode
    "PIL._tkinter_finder",
    "reportlab",
    "reportlab.pdfbase",
    "reportlab.pdfbase._fontdata",
    "reportlab.graphics.barcode",
    # python-multipart (form parsing)
    "multipart",
    # dotenv
    "dotenv",
]

# Collect all data files from packages that ship non-Python assets.
datas = []
# Jinja2 templates + our own templates directory
datas += collect_data_files("jinja2")
# Static files (CSS, JS, fonts, images) — our own directory. uploads are
# bundled too so a fresh install can seed its persistent data-dir copy.
datas += [(os.path.join(PROJECT_ROOT, "static"), "static")]
# Templates directory — own templates. The setup wizard template ships only
# for sales/demo builds; in owner builds the routes are disabled (404).
datas += [(os.path.join(PROJECT_ROOT, "templates"), "templates")]

# Developer-provisioned owner store (DB + .env), staged with:
#   python provision_owner.py --source-db referral.db --source-env .env --stage
# When present, the packaged app self-seeds a fresh data dir from it and
# starts straight into the store — no setup wizard, nothing to configure.
_owner_bundle = os.path.join(PROJECT_ROOT, "owner_bundle")
if os.path.isdir(_owner_bundle):
    datas += [(_owner_bundle, "owner_bundle")]
# Package metadata (needed by some packages at runtime)
datas += copy_metadata("fastapi")
datas += copy_metadata("uvicorn")
datas += copy_metadata("starlette")
datas += copy_metadata("sqlalchemy")
datas += copy_metadata("pydantic")

# Add all submodules from key packages that use dynamic imports
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("sqlalchemy.dialects")
hiddenimports += collect_submodules("PIL")

# ── Analysis ────────────────────────────────────────────────────────────────
a = Analysis(
    ["desktop_entry.py"],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test/dev modules to shrink the bundle
        "pytest",
        "tests",
        "test_image_gen",
        # Developer-only provisioning tool; never include it in a customer app.
        "provision_owner",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── macOS: .app bundle (onedir) ────────────────────────────────────────────
# ── Windows/Linux: onedir (faster startup, more reliable for a server app) ─
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RaykidStore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="logo.ico",  # Uncomment when you have an .ico/.icns icon file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RaykidStore",
)

# On macOS, build a proper .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Raykid Store.app",
        # icon="logo.icns",  # Uncomment when you have an .icns icon
        bundle_identifier="com.raykid.store",
        info_plist={
            "CFBundleDisplayName": "Raykid Store",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "10.13",
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,  # allow http://127.0.0.1
            },
        },
    )
