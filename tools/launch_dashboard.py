"""Open one local SuperWork instance, with no terminal or unrelated process kills."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

try:
    from .superwork import ROOT, NoRedirect
except ImportError:
    from superwork import ROOT, NoRedirect


def request_json(url, *, data=None, headers=None, timeout=2):
    request = Request(url, data=data, headers={"Accept": "application/json", **(headers or {})})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(1_000_001)
    if len(body) > 1_000_000:
        raise ValueError("Respons layanan lokal terlalu besar.")
    return json.loads(body)


def dashboard_ready(base_url):
    try:
        data = request_json(base_url + "/api/health")
        return isinstance(data, dict) and data.get("ok") is True and data.get("app") == "SuperWork"
    except (OSError, ValueError, URLError):
        return False


def ensure_router(base_url):
    try:
        bootstrap = request_json(base_url + "/api/bootstrap", timeout=15)
        token = bootstrap.get("token")
        if not isinstance(token, str) or not token or len(token) > 256:
            return False
        result = request_json(base_url + "/api/router/start", data=b"{}", timeout=35,
                              headers={"Content-Type": "application/json", "Origin": base_url,
                                       "X-SuperWork-Token": token})
        return result.get("ok") is True and result.get("router", {}).get("status") == "ready"
    except (OSError, ValueError, URLError, AttributeError):
        return False


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def log_message(root, message):
    directory = Path(root) / ".superwork"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "launcher.log"
    # Only fixed lifecycle messages, never router output, tokens or provider data.
    if path.exists() and path.stat().st_size > 65_536:
        path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


@contextmanager
def launch_lock(root):
    """Serialize double clicks; the OS releases this lock even after a crash."""
    directory = Path(root) / ".superwork"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "launcher.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 60
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("Peluncuran sebelumnya masih berjalan. Coba lagi sebentar.") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def launch_dashboard(root=ROOT, port=4175, open_browser=True):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Port dashboard harus berupa angka 1 sampai 65535.")
    root = Path(root).resolve()
    base_url = f"http://127.0.0.1:{port}"
    with launch_lock(root):
        reused = dashboard_ready(base_url)
        if not reused:
            if port_open(port):
                raise ValueError(f"Port {port} sedang dipakai aplikasi lain. SuperWork tidak menghentikannya.")
            script = root / "tools" / "dashboard_server.py"
            if not script.is_file():
                raise ValueError("Berkas server SuperWork tidak ditemukan pada folder aplikasi.")
            executable = Path(sys.executable)
            if os.name == "nt" and executable.with_name("pythonw.exe").is_file():
                executable = executable.with_name("pythonw.exe")
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            try:
                process = subprocess.Popen([str(executable), str(script), "--port", str(port)], cwd=str(root),
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, **kwargs)
            except OSError:
                raise ValueError("Dashboard gagal dimulai. Periksa instalasi Python lokal.") from None
            deadline = time.monotonic() + 30
            while not dashboard_ready(base_url):
                if process.poll() is not None:
                    raise ValueError("Dashboard gagal dimulai. Jalankan pemeriksaan instalasi SuperWork.")
                if time.monotonic() >= deadline:
                    raise ValueError("Dashboard belum siap setelah 30 detik. Coba shortcut kembali.")
                time.sleep(0.2)
        router_ready = ensure_router(base_url)
        log_message(root, "Dashboard siap; 9router siap." if router_ready else "Dashboard siap; periksa panel 9router untuk pemulihan.")
        url = base_url + "/#beranda"
        if open_browser:
            try:
                opened = webbrowser.open(url, new=2)
            except (webbrowser.Error, OSError):
                opened = False
            if not opened:
                raise ValueError(f"Dashboard siap. Buka {url} melalui browser Anda.")
        return {"url": url, "reused": reused, "router_ready": router_ready}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Buka dashboard SuperWork tanpa proses ganda.")
    parser.add_argument("--port", type=int, default=4175)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        launch_dashboard(port=arguments.port, open_browser=not arguments.no_browser)
        return 0
    except (ValueError, OSError) as error:
        message = str(error) if isinstance(error, ValueError) else "Peluncur gagal mengakses folder SuperWork. Periksa izin folder."
        try:
            log_message(ROOT, message)
        except OSError:
            pass
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "SuperWork", 0x10)
        elif sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
