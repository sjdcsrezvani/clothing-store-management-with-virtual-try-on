"""Native desktop launcher for the local shop application."""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import webbrowser

from deployment import DESKTOP_PORT, OWNER_MODE, REQUIRE_EXISTING_OWNER_DATA


def _user_data_dir() -> str:
    app_name = "RaykidStore"
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~/.local/share")
    path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def _bundle_dir() -> str:
    return sys._MEIPASS if _is_frozen() else os.path.dirname(os.path.abspath(__file__))  # type: ignore[attr-defined]


def _generate_first_run_env(env_path: str) -> None:
    """Only used by sales/demo builds (OWNER_MODE=False). Owner builds are
    provisioned by the developer and never generate a blank store."""
    import secrets
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("# Auto-generated; configure integrations in the admin panel.\n")
        f.write(f"SESSION_SECRET={secrets.token_hex(32)}\n")
        f.write(f"API_TOKEN={secrets.token_hex(16)}\nADMIN_PASSWORD=\n")


def _seed_from_owner_bundle(data_dir: str) -> bool:
    """Seed a fresh data dir from the owner bundle embedded at build time.

    The bundle is staged by the developer with `python provision_owner.py
    ... --stage` and baked into the packaged app, so it starts with the
    intended store immediately — no setup wizard, nothing for a customer to
    configure. Only fills files that are missing; never overwrites data.
    """
    bundle = os.path.join(_bundle_dir(), "owner_bundle")
    if not os.path.isdir(bundle):
        return False
    seeded = False
    for name in ("referral.db", ".env"):
        src = os.path.join(bundle, name)
        dst = os.path.join(data_dir, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            seeded = True
    return seeded


def _prepare_assets(data_dir: str) -> None:
    """Serve bundled assets, but keep uploads persistent in the data dir.

    Static assets (css/js/fonts/logo) are re-copied from the bundle on every
    launch so app updates always take effect. static/uploads is a real folder
    in the data dir — seeded once from the bundle, then never overwritten.
    (Starlette's StaticFiles rejects symlinked entries that resolve outside
    the mount root, so symlinking individual asset dirs returns 404.)
    """
    static_src = os.path.join(_bundle_dir(), "static")
    static_dst = os.path.join(data_dir, "static")
    if os.path.islink(static_dst):
        os.remove(static_dst)
    os.makedirs(static_dst, exist_ok=True)
    if os.path.isdir(static_src):
        for entry in os.listdir(static_src):
            if entry == "uploads":
                continue
            src = os.path.join(static_src, entry)
            dst = os.path.join(static_dst, entry)
            if os.path.islink(dst) or os.path.isfile(dst):
                os.remove(dst)
            elif os.path.isdir(dst):
                shutil.rmtree(dst)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    uploads_dst = os.path.join(static_dst, "uploads")
    if os.path.islink(uploads_dst):
        os.remove(uploads_dst)
    if not os.path.isdir(uploads_dst):
        uploads_src = os.path.join(static_src, "uploads")
        if os.path.isdir(uploads_src) and os.listdir(uploads_src):
            shutil.copytree(uploads_src, uploads_dst)
        else:
            os.makedirs(uploads_dst)
    for name in ("products", "barcodes", "invoices", "generated", "kid-photos", "temp"):
        os.makedirs(os.path.join(uploads_dst, name), exist_ok=True)
    templates_dst = os.path.join(data_dir, "templates")
    templates_src = os.path.join(_bundle_dir(), "templates")
    if os.path.isdir(templates_src):
        if os.path.islink(templates_dst):
            os.remove(templates_dst)
        if not os.path.exists(templates_dst):
            os.symlink(templates_src, templates_dst)


def _setup_frozen_env() -> str:
    data_dir = _user_data_dir()
    env_file = os.path.join(data_dir, ".env")
    db_path = os.path.join(data_dir, "referral.db")

    # Owner builds self-seed from the developer-embedded store bundle.
    if _is_frozen() and OWNER_MODE and (
        not os.path.exists(env_file) or not os.path.exists(db_path)
    ):
        _seed_from_owner_bundle(data_dir)

    from dotenv import load_dotenv
    if os.path.exists(env_file):
        load_dotenv(env_file, override=False)
    elif OWNER_MODE and REQUIRE_EXISTING_OWNER_DATA and _is_frozen():
        raise RuntimeError(
            "Owner build is not provisioned (no .env in " + data_dir + "). "
            "Run: python provision_owner.py --source-db referral.db --source-env .env"
        )
    else:
        project_env = os.path.join(_bundle_dir(), ".env")
        if OWNER_MODE and not _is_frozen() and os.path.exists(project_env):
            load_dotenv(project_env, override=False)
        else:
            _generate_first_run_env(env_file)
            load_dotenv(env_file, override=False)

    if not os.environ.get("DATABASE_URL"):
        if OWNER_MODE and REQUIRE_EXISTING_OWNER_DATA and _is_frozen() and not os.path.exists(db_path):
            raise RuntimeError(
                "Owner build is not provisioned (no database in " + data_dir + "). "
                "Run: python provision_owner.py --source-db referral.db --source-env .env"
            )
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    os.chdir(data_dir)
    _prepare_assets(data_dir)
    os.makedirs(os.path.join(data_dir, "backups"), exist_ok=True)
    return data_dir


def _find_free_port(host: str = "127.0.0.1") -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _start_server(port: int, host: str):
    import uvicorn
    from main import app
    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning", reload=False, workers=1)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("The local app server did not start")
    return server, thread


def _open_browser_fallback(url: str) -> None:
    webbrowser.open(url)
    print(f"Server running at {url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main() -> None:
    data_dir = _setup_frozen_env()
    port = int(os.environ.get("RAYKID_DESKTOP_PORT", DESKTOP_PORT))
    host_url = f"http://127.0.0.1:{port}"
    lan_url = f"http://{_lan_ip()}:{port}"
    url = f"{host_url}/sales/new"
    print(f"App: {url}")
    print(f"Phone try-on: {lan_url}/admin/mobile")
    try:
        server, thread = _start_server(port, "0.0.0.0")
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} is unavailable. Change DESKTOP_PORT in deployment.py "
            "or set RAYKID_DESKTOP_PORT before starting the app."
        ) from exc

    try:
        import webview

        class DesktopApi:
            """Exposed to pages as window.pywebview.api. Lets the UI do things
            the embedded browser cannot — like opening a second native window
            (target=\"_blank\" does nothing inside pywebview, so a sale can be
            run in a separate window while try-on generation is in flight)."""

            def open_new_sale(self):
                webview.create_window(
                    "فروش جدید — رای کیدز",
                    url=f"{host_url}/sales/new",
                    width=1200, height=780, min_size=(900, 600), text_select=False,
                )
                return True

        api = DesktopApi()
        window = webview.create_window(
            "فروشگاه رای کیدز", url=url, width=1280, height=800,
            min_size=(1024, 600), text_select=False, js_api=api,
        )

        def expose_phone_url():
            # Printed URL is useful for the operator; the mobile page is also directly reachable.
            print(f"Phone try-on URL: {lan_url}/admin/mobile")

        window.events.loaded += expose_phone_url
        webview.start(func=lambda: None)
        server.should_exit = True
        thread.join(timeout=5)
    except ImportError:
        _open_browser_fallback(url)
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
