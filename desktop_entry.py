"""Native desktop launcher for the local shop application."""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import webbrowser

from deployment import APP_SLUG, APP_TITLE, DESKTOP_PORT


def _user_data_dir() -> str:
    """The writable per-user directory: database, .env, uploads and backups."""
    override = os.environ.get("STORE_DATA_DIR")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        os.makedirs(path, exist_ok=True)
        return path
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        # Local, not Roaming: a SQLite database and uploads must stay on the
        # machine and must not be copied by a roaming profile.
        base = (os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~/.local/share")
    path = os.path.join(base, APP_SLUG)
    os.makedirs(path, exist_ok=True)
    return path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def _bundle_dir() -> str:
    return sys._MEIPASS if _is_frozen() else os.path.dirname(os.path.abspath(__file__))  # type: ignore[attr-defined]


def _generate_first_run_env(env_path: str) -> None:
    """Create the local secrets a fresh install needs before setup runs."""
    import secrets
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("# Generated on first launch; edit integrations in the admin panel.\n")
        f.write(f"SESSION_SECRET={secrets.token_hex(32)}\n")
        f.write(f"API_TOKEN={secrets.token_hex(16)}\nADMIN_PASSWORD=\n")


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
        # Copy, never symlink: Windows needs a developer privilege for symlinks,
        # and a copy keeps working when the app folder is moved.
        if os.path.islink(templates_dst) or os.path.isfile(templates_dst):
            os.remove(templates_dst)
        elif os.path.isdir(templates_dst):
            shutil.rmtree(templates_dst)
        shutil.copytree(templates_src, templates_dst)


def _setup_frozen_env() -> str:
    """Point the app at its own data directory and load that install's env."""
    data_dir = _user_data_dir()
    env_file = os.path.join(data_dir, ".env")
    db_path = os.path.join(data_dir, "referral.db")

    from dotenv import load_dotenv
    if not os.path.exists(env_file):
        _generate_first_run_env(env_file)
    load_dotenv(env_file, override=False)

    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    os.chdir(data_dir)
    _prepare_assets(data_dir)
    os.makedirs(os.path.join(data_dir, "backups"), exist_ok=True)
    return data_dir


def _initial_url(host_url: str, data_dir: str) -> str:
    """A fresh install opens on the wizard until setup writes its marker."""
    marker = os.path.join(data_dir, ".setup_done")
    if not os.path.exists(marker):
        return f"{host_url}/admin/setup"
    return f"{host_url}/sales/new"


def _lan_ip() -> str:
    from services.sms_gateway import lan_ip

    return lan_ip()


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


def _start_gateway(port: int):
    """The SMS device gateway on its own listener (:8101).

    The phone polls this port every 15 seconds, so it lives apart from the
    admin UI on purpose: the firewall rule and any port-forward expose only the
    device endpoints, never a single admin page, and a slow device request can
    never queue behind a sale. Same process, same database — one truth about
    what was sent.
    """
    import uvicorn
    from routers.sms_device import gateway_app
    config = uvicorn.Config(app=gateway_app, host="0.0.0.0", port=port,
                            log_level="warning", reload=False, workers=1)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        print(f"SMS gateway could not bind port {port} — پیامک‌ها تا رفع آن در صف می‌مانند.")
        return None, None
    print(f"SMS gateway: http://{_lan_ip()}:{port}  (گوشی به این آدرس وصل می‌شود)")
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
    port = int(os.environ.get("STORE_DESKTOP_PORT", DESKTOP_PORT))
    host_url = f"http://127.0.0.1:{port}"
    lan_url = f"http://{_lan_ip()}:{port}"
    url = _initial_url(host_url, data_dir)
    print(f"App: {url}")
    print(f"Phone try-on: {lan_url}/admin/mobile")
    try:
        server, thread = _start_server(port, "0.0.0.0")
    except OSError as exc:
        raise RuntimeError(
            f"Port {port} is unavailable. Change DESKTOP_PORT in deployment.py "
            "or set STORE_DESKTOP_PORT before starting the app."
        ) from exc

    from services.sms_gateway import gateway_port as _gateway_port

    gateway_port = _gateway_port()
    gateway_server, gateway_thread = _start_gateway(gateway_port)

    try:
        import webview

        class DesktopApi:
            """Exposed to pages as window.pywebview.api. Lets the UI do things
            the embedded browser cannot — like opening a second native window
            (target=\"_blank\" does nothing inside pywebview, so a sale can be
            run in a separate window while try-on generation is in flight)."""

            def open_new_sale(self):
                webview.create_window(
                    "فروش جدید",
                    url=f"{host_url}/sales/new",
                    width=1200, height=780, min_size=(900, 600), text_select=False,
                )
                return True

        api = DesktopApi()
        window = webview.create_window(
            APP_TITLE, url=url, width=1280, height=800,
            min_size=(1024, 600), text_select=False, js_api=api,
        )

        def expose_phone_url():
            # Printed URL is useful for the operator; the mobile page is also directly reachable.
            print(f"Phone try-on URL: {lan_url}/admin/mobile")

        window.events.loaded += expose_phone_url
        webview.start(func=lambda: None)
        server.should_exit = True
        thread.join(timeout=5)
        if gateway_server is not None:
            gateway_server.should_exit = True
            gateway_thread.join(timeout=5)
    except ImportError:
        _open_browser_fallback(url)
        server.should_exit = True
        thread.join(timeout=5)
        if gateway_server is not None:
            gateway_server.should_exit = True
            gateway_thread.join(timeout=5)


def _startup_error_path() -> str:
    try:
        return os.path.join(_user_data_dir(), "startup-error.log")
    except OSError:
        return os.path.join(os.path.expanduser("~"), "StoreManagement-startup-error.log")


def _report_startup_error(exc: BaseException) -> None:
    """A windowed build has no console: leave a log and tell the user on Windows."""
    import traceback
    path = _startup_error_path()
    try:
        with open(path, "w", encoding="utf-8") as log:
            log.write("".join(traceback.format_exception(exc)))
    except OSError:
        path = ""
    if sys.platform == "win32":
        try:
            import ctypes
            detail = f"\n\n{exc}\n\nLog: {path}" if path else f"\n\n{exc}"
            ctypes.windll.user32.MessageBoxW(0, detail, APP_TITLE, 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _report_startup_error(exc)
        raise
