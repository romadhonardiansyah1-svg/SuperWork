"""Own only SuperWork's router process; never run the CLI's global kill helpers.

9router 0.5.59's CLI kills matching Node/Next processes before starting. We use
its installed, bundled server and official runtime environment hook directly.
"""
from collections import deque
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

try:
    from .superwork import DEFAULT_URL, ROOT, NoRedirect, local_url
except ImportError:
    from superwork import DEFAULT_URL, ROOT, NoRedirect, local_url


def router_command():
    """Resolve only the installed package; no shell, downloads, or updates."""
    shim, node = shutil.which("9router"), shutil.which("node")
    if not shim or not node:
        raise ValueError("Node.js atau 9router belum tersedia di PATH.")
    parent = Path(shim).resolve().parent
    candidates = (parent / "node_modules" / "9router", parent, parent.parent)
    for package in candidates:
        metadata = package / "package.json"
        try:
            if metadata.stat().st_size > 65_536 or json.loads(metadata.read_text(encoding="utf-8")).get("name") != "9router":
                continue
        except (OSError, ValueError, AttributeError):
            continue
        hook = package / "hooks" / "sqliteRuntime.js"
        server = package / "app" / "custom-server.js"
        if not server.is_file():
            server = package / "app" / "server.js"
        if hook.is_file() and server.is_file():
            # custom-server starts only when it is the main module, never via require().
            return [node, "--dns-result-order=ipv4first", "--max-old-space-size=6144", str(server)]
    raise ValueError("Server bawaan 9router belum tersedia pada instalasi lokal.")


def router_environment(command):
    """Set the official NODE_PATH before Node initializes module search paths."""
    hook = Path(command[-1]).parent.parent / "hooks" / "sqliteRuntime.js"
    script = "process.stdout.write(require(process.argv[1]).buildEnvWithRuntime(process.env).NODE_PATH);"
    try:
        result = subprocess.run([command[0], "-e", script, str(hook)], capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        value = result.stdout.decode("utf-8")
        if result.returncode or not value or len(value) > 32768 or re.search(r"[\x00\r\n]", value):
            raise ValueError
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ValueError("Lingkungan runtime 9router tidak dapat disiapkan.") from None
    return dict(os.environ, NODE_PATH=value)


def _windows_identity(handle, pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    exit_code = wintypes.DWORD()
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
        return None
    times = [wintypes.FILETIME() for _ in range(4)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    length = wintypes.DWORD(len(buffer))
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
        return None
    return {"pid": pid, "created": str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime),
            "executable": os.path.normcase(buffer.value)}


def process_identity(pid):
    """A PID alone never proves ownership: include creation time and executable."""
    if type(pid) is not int or not 0 < pid < 2**32:
        return None
    if os.name != "nt":
        try:
            directory = Path("/proc") / str(pid)
            return {"pid": pid, "created": str(directory.stat().st_ctime_ns),
                    "executable": str((directory / "exe").resolve(strict=True))}
        except OSError:
            return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        return _windows_identity(handle, pid)
    finally:
        kernel.CloseHandle(handle)


def terminate_record(record):
    """On Windows verify and terminate the same open handle, avoiding PID reuse."""
    if os.name != "nt":
        if process_identity(record.get("pid")) != record:
            return False
        os.kill(record["pid"], 15)
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000 | 0x0001 | 0x00100000, False, record["pid"])
    if not handle:
        return False
    try:
        if _windows_identity(handle, record["pid"]) != record:
            return False
        if not kernel.TerminateProcess(handle, 0):
            return False
        return kernel.WaitForSingleObject(handle, 8000) == 0
    finally:
        kernel.CloseHandle(handle)


class RouterRuntime:
    def __init__(self, root=ROOT, base_url=DEFAULT_URL):
        self.root = Path(root).resolve()
        self.base_url = local_url(base_url)
        parsed = urlsplit(self.base_url)
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.record_path = self.root / ".superwork" / "router-process.json"
        self._process = None
        self._lock = threading.RLock()
        self._events = deque(maxlen=100)
        self._error = ""

    def _log(self, message):
        self._events.append(f"{datetime.now().isoformat(timespec='seconds')} {message}")

    def logs(self):
        with self._lock:
            return list(self._events)

    def _probe(self):
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(
                    Request(self.base_url + "/models", headers={"Accept": "application/json"}), timeout=1) as response:
                body = response.read(1_000_001)
            document = json.loads(body) if len(body) <= 1_000_000 else None
            return isinstance(document, dict) and isinstance(document.get("data"), list)
        except HTTPError as error:
            code = error.code
            error.close()
            return code in (401, 403)
        except (URLError, OSError, ValueError):
            return False

    def _port_open(self):
        try:
            with socket.create_connection((urlsplit(self.base_url).hostname, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def _record(self):
        try:
            if self.record_path.stat().st_size > 4096:
                return None
            record = json.loads(self.record_path.read_text(encoding="utf-8"))
            if (isinstance(record, dict) and set(record) == {"pid", "created", "executable"}
                    and record == process_identity(record.get("pid"))):
                return record
        except (OSError, ValueError):
            pass
        return None

    def _owned(self):
        if self._process is not None:
            if self._process.poll() is None:
                return True
            self._process = None
            self.record_path.unlink(missing_ok=True)
        return self._record() is not None

    def status(self):
        with self._lock:
            managed = self._owned()
            ready = self._probe()
            if ready:
                status = "ready"
                message = ("9router siap digunakan." if managed else
                           "9router berjalan di luar SuperWork; penghentian dikelola pemilik proses.")
            else:
                status = "error" if self._error else "starting" if managed else "stopped"
                message = self._error or ("Menunggu 9router siap." if managed else "9router belum menyala.")
            return {"status": status, "managed": managed, "can_stop": managed,
                    "base_url": self.base_url, "message": message}

    def start(self):
        with self._lock:
            current = self.status()
            if current["status"] == "ready" or current["managed"]:
                return current
            if self._port_open():
                self._error = f"Port {self.port} sedang dipakai layanan lain. Layanan tersebut tidak dihentikan."
                return self.status()
            if urlsplit(self.base_url).scheme != "http":
                self._error = "Server bawaan 9router menggunakan HTTP loopback."
                return self.status()
            try:
                command = router_command()
                env = dict(router_environment(command), HOSTNAME="127.0.0.1", PORT=str(self.port), NODE_ENV="production")
                kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                self._process = subprocess.Popen(command, cwd=str(self.root), env=env, stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
                identity = process_identity(self._process.pid)
                if identity:
                    self.record_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self.record_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(identity), encoding="utf-8")
                    temporary.replace(self.record_path)
                self._error = ""
                self._log("Menyalakan 9router milik SuperWork pada loopback.")
            except (OSError, ValueError):
                self._error = "9router gagal dinyalakan. Pastikan Node.js dan paket 9router terpasang."
                self._log(self._error)
                return self.status()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if self._probe():
                    self._log("9router siap.")
                    return self.status()
                if self._process.poll() is not None:
                    self._error = "Proses 9router berhenti sebelum siap. Periksa instalasi dan pengaturan provider."
                    break
                time.sleep(0.2)
            else:
                self._log("9router masih menyiapkan layanan setelah 25 detik; periksa status berikutnya.")
                return self.status()
            self._log(self._error)
            return self.status()

    def stop(self):
        with self._lock:
            owned = self._owned()
            if not owned:
                return self.status()
            try:
                if self._process is not None and self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=2)
                else:
                    record = self._record()
                    if record is None or not terminate_record(record):
                        raise OSError("Ownership could not be verified.")
                self._process = None
                self.record_path.unlink(missing_ok=True)
                self._error = ""
                self._log("9router milik SuperWork dihentikan; dashboard tetap berjalan.")
            except (OSError, subprocess.TimeoutExpired):
                self._error = "9router belum dapat dihentikan dengan aman. Coba periksa status kembali."
                self._log(self._error)
            return self.status()

    def restart(self):
        with self._lock:
            if self._probe() and not self._owned():
                return self.status()
            stopped = self.stop()
            if stopped["managed"]:
                return stopped
            return self.start()
