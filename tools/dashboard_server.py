"""SuperWork loopback HTTP application. Run via the Windows shortcut for daily use."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
from urllib.parse import quote, urlsplit

try:
    from .dashboard_ai import available_models, check_model, run_task
    from .dashboard_store import DashboardStore, StateConflictError, MAX_STATE
    from .dashboard_integrations import IntegrationRuntime, IntegrationError, PORTALS
    from .router_runtime import RouterRuntime
    from .superwork import ROOT
except ImportError:
    from dashboard_ai import available_models, check_model, run_task
    from dashboard_store import DashboardStore, StateConflictError, MAX_STATE
    from dashboard_integrations import IntegrationRuntime, IntegrationError, PORTALS
    from router_runtime import RouterRuntime
    from superwork import ROOT

VERSION = '1.0.0'
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


def reject_json_constant(value):
    raise ValueError('JSON constants must be finite.')


class APIError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, *, root=ROOT, data_dir=None, router=None, models_provider=None, integrations=None):
        if address[0] != '127.0.0.1':
            raise ValueError('SuperWork hanya boleh mendengarkan 127.0.0.1.')
        self.root = Path(root).resolve()
        self.store = DashboardStore(data_dir or self.root / '.superwork')
        self.integrations = integrations or IntegrationRuntime(self.store.root)
        self.router = router or RouterRuntime(self.root)
        self.models_provider = models_provider or (lambda: available_models(self.router.base_url))
        self.token = secrets.token_urlsafe(32)
        self.ai_busy = threading.BoundedSemaphore(1)
        self.stopping = False
        super().__init__(address, DashboardHandler)

    def models(self):
        try:
            return {'models': self.models_provider(), 'models_error': None}
        except ValueError as exc:
            return {'models': [], 'models_error': str(exc)}

    def server_close(self):
        try:
            self.integrations.close()
        finally:
            super().server_close()

    def finish(self):
        try:
            if self.router.status().get('managed'):
                self.router.stop()
        except (OSError, ValueError):
            # The server still closes; runtime keeps verifiable process metadata for recovery.
            pass
        self.shutdown()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = 'SuperWork'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        # URLs, request bodies, candidate names and provider payloads never enter the access log.
        return

    def _headers(self, code, content_type, length, filename=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy', CSP)
        if filename:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename, safe=''))
        self.end_headers()

    def _json(self, value, code=200, filename=None):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self._headers(code, 'application/json; charset=utf-8', len(raw), filename)
        self.wfile.write(raw)

    def _guard(self, mutating=False):
        if mutating and self.server.stopping:
            raise APIError('SuperWork sedang ditutup. Buka kembali melalui shortcut setelah selesai.', 409)
        port = self.server.server_address[1]
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            raise APIError('Host dashboard ditolak.', 403)
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            raise APIError('Origin dashboard ditolak.', 403)
        path = urlsplit(self.path).path
        if path.startswith('/api/') and self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise APIError('Akses lintas situs ditolak.', 403)
        if mutating:
            token = self.headers.get('X-SuperWork-Token', '')
            if not secrets.compare_digest(token, self.server.token):
                raise APIError('Sesi tidak sesuai. Muat ulang dashboard sebelum melanjutkan.', 403)
            if self.headers.get_content_type() != 'application/json':
                raise APIError('Gunakan application/json.', 415)

    def _body(self, limit=3_500_000):
        length = self.headers.get('Content-Length', '')
        if self.headers.get('Transfer-Encoding') or not length.isascii() or not length.isdecimal():
            raise APIError('Ukuran permintaan tidak valid.', 411)
        size = int(length)
        if size <= 0 or size > limit:
            raise APIError('Permintaan kosong atau melewati batas ukuran.', 413)
        try:
            raw = self.rfile.read(size)
            self._body_consumed = True
            if len(raw) != size:
                raise ValueError
            data = json.loads(raw, parse_constant=reject_json_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError('JSON permintaan tidak valid.') from None
        if not isinstance(data, dict):
            raise APIError('Permintaan harus berupa objek JSON.')
        return data

    def _run(self, operation):
        self._body_consumed = False
        try:
            self._guard(self.command in ('POST', 'PUT', 'DELETE'))
            operation()
        except (APIError, IntegrationError, StateConflictError) as exc:
            # Drain small rejected bodies before closing: otherwise Windows may reset
            # the socket while the client is still sending, hiding the intended 403.
            length = self.headers.get('Content-Length', '')
            if not self._body_consumed and length.isascii() and length.isdecimal() and 0 < int(length) <= 32768:
                try:
                    self.connection.settimeout(1)
                    self.rfile.read(int(length))
                except (OSError, TimeoutError):
                    pass
            self._json({'ok': False, 'error': str(exc)}, 409 if isinstance(exc, StateConflictError) else exc.status)
        except ValueError as exc:
            self._json({'ok': False, 'error': str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return
        except Exception as exc:
            # Record only the class, not its text or traceback (which may contain user data).
            self.server.store.root.mkdir(parents=True, exist_ok=True)
            with (self.server.store.root / 'errors.log').open('a', encoding='utf-8') as log:
                log.write(datetime.now(timezone.utc).isoformat() + ' ' + type(exc).__name__ + '\n')
            self._json({'ok': False, 'error': 'Layanan lokal mengalami kesalahan. Data belum dianggap berhasil; lihat Pengaturan atau mulai ulang SuperWork.'}, 500)

    def do_GET(self):
        self._run(self._get)

    def _get(self):
        path = urlsplit(self.path).path
        if path == '/api/health':
            return self._json({'ok': True, 'app': 'SuperWork', 'version': VERSION})
        if path == '/api/bootstrap':
            try:
                from .dashboard_jobs import SOURCE_DEFINITIONS
            except ImportError:
                from dashboard_jobs import SOURCE_DEFINITIONS
            snapshot = self.server.store.snapshot()
            return self._json({'ok': True, 'version': VERSION, 'token': self.server.token,
                               **snapshot, 'router': self.server.router.status(),
                               **self.server.models(), 'job_sources': SOURCE_DEFINITIONS,
                               'capabilities': {'portal_integrations': True, 'files': ['pdf', 'docx', 'txt'], 'max_file_bytes': 8 * 1024 * 1024,
                               'ai_tasks': ['chat', 'fit', 'document', 'interview', 'learning', 'profile', 'feedback'], 'gmail': False, 'notion': False}})
        if path == '/api/router':
            return self._json({'ok': True, 'router': self.server.router.status()})
        if path == '/api/integrations':
            return self._json({'ok': True, 'providers': self.server.integrations.snapshot()})
        if path == '/api/router/logs':
            return self._json({'ok': True, 'lines': self.server.router.logs()})
        if path == '/api/models':
            return self._json({'ok': True, 'router': self.server.router.status(), **self.server.models()})
        if path == '/api/backup':
            return self._json(self.server.store.backup(), filename='SuperWork-cadangan.json')
        if path.startswith('/api/files/'):
            metadata, raw = self.server.store.get_file(path.removeprefix('/api/files/'))
            self._headers(200, metadata['type'], len(raw), metadata['name'])
            return self.wfile.write(raw)
        static = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}
        if path == '/favicon.ico':
            return self._headers(204, 'image/x-icon', 0)
        if path in static:
            file = self.server.root / 'dashboard' / static[path]
            if not file.is_file():
                raise APIError('Berkas dashboard belum tersedia.', 503)
            raw = file.read_bytes()
            mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            self._headers(200, mime + '; charset=utf-8', len(raw))
            return self.wfile.write(raw)
        raise APIError('Halaman atau endpoint tidak ditemukan.', 404)

    def do_PUT(self):
        self._run(self._put)

    def _expected_revision(self):
        value = self.headers.get('If-Match', '')
        if not re.fullmatch(r'"[0-9a-f]{64}"', value):
            raise StateConflictError
        return value[1:-1]

    def _put(self):
        if urlsplit(self.path).path != '/api/state':
            raise APIError('Endpoint tidak ditemukan.', 404)
        data = self._body(MAX_STATE + 100)
        revision = self._expected_revision()
        with self.server.store.lock:
            self.server.store.save_state(data.get('state'), expected_revision=revision)
            snapshot = self.server.store.snapshot()
        self._json({'ok': True, **snapshot})

    def do_DELETE(self):
        self._run(self._delete)

    def _delete(self):
        path = urlsplit(self.path).path
        if not path.startswith('/api/files/'):
            raise APIError('Endpoint tidak ditemukan.', 404)
        self._body(1000)
        revision = self._expected_revision()
        with self.server.store.lock:
            self.server.store.delete_file(path.removeprefix('/api/files/'), expected_revision=revision)
            snapshot = self.server.store.snapshot()
        self._json({'ok': True, **snapshot})

    def do_POST(self):
        self._run(self._post)

    def _post(self):
        path = urlsplit(self.path).path
        limit = 120_000_000 if path == '/api/restore' else 11_500_000 if path == '/api/files' else 250_000
        data = self._body(limit)
        if path.startswith('/api/integrations/'):
            parts = path.split('/')
            if len(parts) != 5 or parts[3] not in PORTALS or parts[4] not in ('open', 'check', 'sync', 'disconnect'):
                raise APIError('Portal atau tindakan integrasi tidak dikenal.')
            provider_id, action = parts[3:]
            fields = {'open': set(), 'check': {'query'}, 'sync': {'query', 'filters'}, 'disconnect': {'confirm'}}[action]
            if set(data) - fields or action == 'disconnect' and data.get('confirm') is not True:
                raise APIError('Isian integrasi tidak valid atau konfirmasi keluar belum diberikan.')
            if action in ('check', 'sync'):
                try:
                    from .dashboard_jobs import _search_inputs
                except ImportError:
                    from dashboard_jobs import _search_inputs
                query, filters, _ = _search_inputs(data.get('query'), data.get('filters', {}), [provider_id], [])
            if action == 'sync':
                return self._json(self.server.integrations.sync(provider_id, query, filters))
            if action == 'open':
                provider = self.server.integrations.open(provider_id)
            elif action == 'check':
                provider = self.server.integrations.check(provider_id, query)
            else:
                provider = self.server.integrations.disconnect(provider_id, confirm=True)
            return self._json({'ok': True, 'provider': provider})
        if path == '/api/documents/pdf':
            document_id = data.get('id')
            if set(data) != {'id'} or type(document_id) is not int or not 0 < document_id <= 2**53 - 1:
                raise APIError('Kirim hanya ID dokumen tersimpan berupa angka positif yang valid.')
            document = next((item for item in self.server.store.load_state().get('documents', [])
                             if item.get('id') == document_id), None)
            if document is None:
                raise APIError('Dokumen tidak ditemukan. Muat ulang daftar dokumen.', 404)
            try:
                from .dashboard_pdf import render_document_pdf
            except ImportError:
                from dashboard_pdf import render_document_pdf
            filename, raw = render_document_pdf(document)
            self._headers(200, 'application/pdf', len(raw), filename)
            return self.wfile.write(raw)
        if path in ('/api/router/start', '/api/router/stop', '/api/router/restart'):
            result = getattr(self.server.router, path.rsplit('/', 1)[1])()
            return self._json({'ok': True, 'router': result})
        if path == '/api/files':
            return self._json({'ok': True, 'file': self.server.store.add_file(data.get('name'), data.get('content_base64'))})
        if path == '/api/restore':
            revision = self._expected_revision()
            with self.server.store.lock:
                self.server.store.restore(data, expected_revision=revision)
                snapshot = self.server.store.snapshot()
            return self._json({'ok': True, **snapshot})
        if path == '/api/search':
            try:
                from .dashboard_jobs import search_jobs
            except ImportError:
                from dashboard_jobs import search_jobs
            return self._json(search_jobs(data.get('query', ''), data.get('filters'), data.get('sources'), data.get('boards'),
                                          portal_search=self.server.integrations.read_portal, cursors=data.get('cursors')))
        if path == '/api/verify':
            try:
                from .dashboard_jobs import verify_source
            except ImportError:
                from dashboard_jobs import verify_source
            return self._json(verify_source(data.get('url', '')))
        if path in ('/api/ai', '/api/check'):
            if not self.server.ai_busy.acquire(blocking=False):
                raise APIError('Satu tugas AI masih berjalan. Tunggu hasilnya sebelum memulai tugas berikutnya.', 409)
            try:
                if path == '/api/check':
                    result = check_model(data.get('model'), base_url=self.server.router.base_url)
                else:
                    result = run_task(data.get('model'), data.get('task'), data.get('context', {}), data.get('message', ''), base_url=self.server.router.base_url)
                return self._json(result)
            finally:
                self.server.ai_busy.release()
        if path == '/api/shutdown':
            if not self.server.ai_busy.acquire(blocking=False):
                raise APIError('Tugas AI masih berjalan. Tunggu hasilnya sebelum menutup SuperWork.', 409)
            self.server.stopping = True
            self._json({'ok': True})
            threading.Thread(target=self.server.finish, daemon=True).start()
            return
        raise APIError('Endpoint tidak ditemukan.', 404)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4175)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('port harus antara 1024 dan 65535')
    try:
        with DashboardHTTPServer(('127.0.0.1', args.port)) as server:
            print(f'SuperWork: http://127.0.0.1:{args.port}', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                if server.router.status().get('managed'):
                    server.router.stop()
    except OSError:
        print('SuperWork tidak dapat membuka port. Buka shortcut atau periksa layanan lain yang menggunakan port tersebut.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
