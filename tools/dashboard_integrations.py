"""Local portal browsers. Credentials stay inside dedicated native Edge profiles."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

try:
    from playwright.sync_api import Error as BrowserError
except ImportError:
    class BrowserError(Exception):
        pass

try:
    from .dashboard_jobs import (FetchError, _base_job, _fetch, _resolve_public, _search_inputs, _stamp,
                                 _validate_url, _portal_job_url, _matches, _text, _portal_availability,
                                 _MetadataParser, _job_postings, _select_posting, _canonical_url)
    from .page_decode import decode_page
except ImportError:
    from dashboard_jobs import (FetchError, _base_job, _fetch, _resolve_public, _search_inputs, _stamp,
                                _validate_url, _portal_job_url, _matches, _text, _portal_availability,
                                _MetadataParser, _job_postings, _select_posting, _canonical_url)
    from page_decode import decode_page


PORTALS = {
    'glints': {'name': 'Glints', 'login': 'https://glints.com/id/login', 'mode': 'public_page', 'host': 'glints.com'},
    'jobstreet': {'name': 'Jobstreet', 'login': 'https://id.jobstreet.com/id/oauth/login?returnUrl=%2F',
                  'mode': 'local_browser', 'host': 'id.jobstreet.com'},
}
LOGIN_HOSTS = {'accounts.google.com', 'www.facebook.com', 'appleid.apple.com',
               'account.live.com', 'login.microsoftonline.com', 'auth.seek.com', 'login.seek.com'}
PERSISTED_FIELDS = ('access_status', 'message', 'checked_at', 'last_sync_at', 'result_count', 'last_query',
                    'login_status', 'login_checked_at')


def _portal_next_url(base, value):
    url = _validate_url(urljoin(base, value))
    origin, parts = urlsplit(base), urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    params = dict(pairs)
    if (parts.scheme != 'https' or parts.netloc != origin.netloc or parts.path != origin.path
            or len(params) != len(pairs) or set(params) != {'page'}
            or not re.fullmatch(r'[1-9]\d{0,8}', params['page'])):
        raise FetchError('invalid_cursor', 'Halaman berikutnya tidak sesuai pencarian portal.')
    return url


def _google_next_url(base, value):
    parts, original = urlsplit(urljoin(base, value)), urlsplit(base)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    params, initial = dict(pairs), dict(parse_qsl(original.query))
    if (parts.scheme != 'https' or parts.hostname not in ('www.google.com', 'google.com')
            or parts.port not in (None, 443) or parts.username or parts.password or parts.path != '/search'
            or len(params) != len(pairs) or params.get('q') != initial.get('q')
            or not re.fullmatch(r'[1-9]\d{0,8}', params.get('start', ''))):
        raise FetchError('invalid_cursor', 'Halaman berikutnya Google tidak sesuai pencarian.')
    # Search tracking identifiers are unnecessary for pagination and are never persisted.
    return 'https://www.google.com/search?' + urlencode({'q': initial['q'], 'start': params['start']})


def _scroll_cursor(url, count):
    return 'scroll:' + hashlib.sha256(url.encode()).hexdigest()[:16] + ':' + str(count)


def _scroll_offset(url, cursor):
    if cursor is None:
        return 0
    prefix = _scroll_cursor(url, 0).rsplit(':', 1)[0] + ':'
    if not isinstance(cursor, str) or not cursor.startswith(prefix) or not re.fullmatch(r'[1-9]\d{0,8}', cursor[len(prefix):]):
        raise FetchError('invalid_cursor', 'Kelanjutan Glints berasal dari pencarian lain atau tidak valid.')
    return int(cursor[len(prefix):])


def _glints_card_jobs(cards, query, filters, checked, source_url):
    jobs, seen = [], set()
    for row in cards:
        link = _portal_job_url('glints', row.get('url'))
        if not link or link[0] in seen:
            continue
        seen.add(link[0])
        location = _text(row.get('location'), 300)
        job = _base_job({'name': 'Glints', 'api': source_url}, checked, title=row.get('title'), company=row.get('company'),
                        url=link[0], source_id=link[1], description=row.get('description'),
                        location=location, countries=['Indonesia'], cities=[location.split(',')[0]],
                        employment=row.get('employment'), sector=row.get('sector'))
        if job:
            job.update(providerId='glints', searchQuery=query)
            job['evidence'].update(method='local_browser', source_page=source_url,
                                  message='Kartu pencarian Glints dibaca di browser. Status penerimaan belum diperiksa di detail.')
            job['evidenceText'] = job['evidence']['message']
            if _matches(job, query, filters):
                jobs.append(job)
    return jobs


def _discover_web_job(row, query, checked):
    try:
        url = _validate_url(row.get('url'))
    except (FetchError, TypeError):
        return None
    domain = urlsplit(url).hostname
    source = {'name': domain, 'sourceType': 'web', 'api': 'https://www.google.com/search?' + urlencode({'q': query})}
    job = _base_job(source, checked, title=row.get('title'), company='', url=url, description=row.get('description'))
    if not job:
        return None
    job.update(kind='web_result', foundOn='Google', searchQuery=query)
    job['evidence'].update(method='web_search', message='Ditemukan di Google. Halaman ini belum dikonfirmasi sebagai satu lowongan.')
    try:
        response = _fetch(url, accept='text/html', check_robots=True, max_bytes=2 * 1024 * 1024)
        job['evidence']['http_status'] = response['status']
        if response['status'] in (404, 410):
            job['status'] = 'closed'
        if response['status'] == 200 and 'html' in response['headers'].get('content-type', ''):
            parser = _MetadataParser()
            parser.feed(decode_page(response['body']))
            posting = _select_posting(_job_postings(parser.documents), response['url'])
            if posting and posting.get('url'):
                try:
                    posting_url = _validate_url(urljoin(response['url'], posting['url']))
                    if _canonical_url(posting_url) != _canonical_url(response['url']):
                        posting = None
                except (FetchError, TypeError, ValueError):
                    posting = None
            if posting and not parser.password_input:
                org = posting.get('hiringOrganization')
                org = org if isinstance(org, dict) else {}
                places = posting.get('jobLocation', [])
                places = places if isinstance(places, list) else [places]
                addresses = [p.get('address', {}) for p in places if isinstance(p, dict)]
                addresses = [a for a in addresses if isinstance(a, dict)]
                countries = [_text(a.get('addressCountry')) for a in addresses if isinstance(a.get('addressCountry'), str)]
                cities = [_text(a.get('addressLocality')) for a in addresses if a.get('addressLocality')]
                parsed = _base_job(source, checked, title=posting.get('title'), company=org.get('name'),
                                   url=url, description=posting.get('description'), countries=countries, cities=cities,
                                   location=', '.join(cities + countries), employment=posting.get('employmentType'),
                                   published=posting.get('datePosted'), mode=posting.get('jobLocationType'))
                if parsed:
                    job = parsed
                    job.update(foundOn='Google', searchQuery=query)
                    _portal_availability(job, None, posting.get('validThrough'), checked)
                    employer_url = org.get('sameAs') or org.get('url')
                    try:
                        employer_host = urlsplit(_validate_url(employer_url)).hostname
                    except FetchError:
                        employer_host = None
                    actual_host = urlsplit(response['url']).hostname or ''
                    # This is a source-location classification, not identity verification.
                    if employer_host and (actual_host == employer_host or actual_host.endswith('.' + employer_host)):
                        job['sourceType'] = 'company'
                    job['evidence'].update(method='public_page', http_status=200, source_page=response['url'],
                                          message='Metadata JobPosting ditemukan dari hasil Google. Identitas penerbit dan penerimaan lamaran perlu diperiksa.')
    except FetchError as error:
        job['evidence']['message'] += ' ' + str(error)
    job['evidence']['status'] = job['status']
    job['evidenceText'] = job['evidence']['message']
    return job


class IntegrationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _provider_id(value):
    if not isinstance(value, str) or value not in PORTALS:
        raise IntegrationError('Portal integrasi tidak dikenal.')
    return value


def _default(provider_id):
    return {'id': provider_id, 'name': PORTALS[provider_id]['name'], 'mode': PORTALS[provider_id]['mode'],
            'browser_open': False, 'session_saved': False, 'login_status': 'not_checked', 'login_checked_at': None,
            'access_status': 'not_checked', 'message': 'Belum diperiksa. Hubungkan browser untuk login sendiri.',
            'checked_at': None, 'last_sync_at': None, 'result_count': None, 'last_query': ''}


def _reparse(path):
    return path.exists() and bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) if os.name == 'nt' else path.is_symlink()


def _launch_login_browser(profile, url):
    candidates = [Path(os.environ.get(key, '')) / 'Microsoft/Edge/Application/msedge.exe'
                  for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES') if os.environ.get(key)]
    executable = next((path for path in candidates if path.is_file()), None)
    if os.name != 'nt' or executable is None:
        raise IntegrationError('Microsoft Edge terpasang diperlukan untuk login manual di Windows.', 503)
    return subprocess.Popen([str(executable), f'--user-data-dir={profile}', '--disable-background-mode',
                             '--new-window', url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class IntegrationRuntime:
    def __init__(self, data_dir, *, browser_factory=None, login_launcher=None):
        self.root = Path(data_dir).resolve()
        self.profile_root = self.root / 'portal-browser'
        self.metadata_path = self.root / 'portal-integrations.json'
        self._factory = browser_factory
        self._login_launcher = login_launcher or _launch_login_browser
        self._login_processes = {}
        self._driver = None
        self._route_handler = self._navigation
        self._contexts = {}
        self._glints_search = None
        self._metadata = {key: _default(key) for key in PORTALS}
        self._loaded = False
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='SuperWork-portals')
        self._pending = 0
        self._closed = False

    def _load(self):
        if self._loaded:
            return
        if self.metadata_path.exists():
            try:
                if self.metadata_path.stat().st_size > 32000:
                    raise ValueError
                data = json.loads(self.metadata_path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or set(data) - set(PORTALS):
                    raise ValueError
                for key, values in data.items():
                    if not isinstance(values, dict) or set(values) - set(PERSISTED_FIELDS):
                        raise ValueError
                    for field, value in values.items():
                        if field == 'login_status':
                            if value not in ('not_checked', 'signed_in', 'signed_out', 'unknown'):
                                raise ValueError
                        elif field == 'result_count':
                            if value is not None and (type(value) is not int or not 0 <= value <= 5000):
                                raise ValueError
                        elif value is not None and (not isinstance(value, str) or len(value) > 600):
                            raise ValueError
                    self._metadata[key].update(values)
            except (ValueError, UnicodeError, OSError):
                raise IntegrationError('Metadata integrasi tidak dapat dibaca. Pertahankan profil browser dan periksa portal-integrations.json.', 503) from None
        self._loaded = True

    def _save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {key: {field: value[field] for field in PERSISTED_FIELDS} for key, value in self._metadata.items()}
        descriptor, name = tempfile.mkstemp(prefix='.portal-', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(payload, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.metadata_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _profile(self, provider_id):
        path = self.profile_root / _provider_id(provider_id)
        if _reparse(self.profile_root) or _reparse(path) or path.resolve().parent != self.profile_root.resolve():
            raise IntegrationError('Lokasi profil browser tidak aman.', 409)
        return path

    def snapshot(self):
        with self._lock:
            self._load()
            login_browsers = {key: self._login_running(key) for key in PORTALS}
            rows = deepcopy(self._metadata)
            for key, row in rows.items():
                row['session_saved'] = self._profile(key).is_dir()
                row['login_browser_open'] = login_browsers[key]
                if not row['session_saved'] and row['login_status'] == 'signed_in':
                    row['login_status'] = 'unknown'
            return list(rows.values())

    def _login_running(self, provider_id):
        with self._lock:
            process = self._login_processes.get(provider_id)
            if process is None:
                return False
            if process.poll() is None:
                return True
            self._login_processes.pop(provider_id, None)
            self._metadata[provider_id]['browser_open'] = False
            return False

    def _snapshot_one(self, provider_id):
        return next(row for row in self.snapshot() if row['id'] == provider_id)

    def _call(self, operation, *args):
        with self._lock:
            self._load()
            if self._closed:
                raise IntegrationError('Browser integrasi sudah ditutup. Buka kembali SuperWork.', 503)
            if self._pending >= 3:
                raise IntegrationError('Browser integrasi sedang bekerja. Tunggu hasilnya dahulu.', 409)
            self._pending += 1
            future = self._executor.submit(operation, *args)
            def finished(_):
                with self._lock:
                    self._pending -= 1
            future.add_done_callback(finished)
        try:
            return future.result(timeout=55)
        except FutureTimeout:
            raise IntegrationError('Browser belum selesai. Periksa jendela portal dan coba lagi setelah proses berakhir.', 504) from None

    def _navigation(self, route):
        request = route.request
        parts = urlsplit(request.url)
        host = (parts.hostname or '').lower()
        try:
            private = not ipaddress.ip_address(host).is_global
        except ValueError:
            private = host == 'localhost' or host.endswith(('.local', '.localhost', '.internal'))
        if private:
            return route.abort()
        if request.is_navigation_request() and request.frame.parent_frame is None:
            allowed = {portal['host'] for portal in PORTALS.values()} | LOGIN_HOSTS | {'www.google.com', 'google.com'}
            if parts.scheme != 'https' or host not in allowed or parts.port not in (None, 443):
                return route.abort()
        route.continue_()

    def _context(self, provider_id):
        if self._login_running(provider_id):
            raise FetchError('action_required', 'Selesaikan login, lalu tutup jendela Edge portal. Setelah itu klik Periksa akses; profil tetap tersimpan.')
        context = self._contexts.get(provider_id)
        if context is not None:
            pages = [page for page in context.pages if not page.is_closed()]
            if pages:
                return context
            self._contexts.pop(provider_id, None)
        path = self._profile(provider_id)
        path.mkdir(parents=True, exist_ok=True)
        self._start_driver()
        try:
            context = self._driver.chromium.launch_persistent_context(str(path), channel='msedge', headless=False,
                                                                       timeout=15000, accept_downloads=False)
        except (BrowserError, OSError):
            # Driver messages can contain URLs or profile paths. Never return them to the client.
            raise IntegrationError('Edge tidak dapat dibuka. Tutup jendela profil portal yang tertinggal lalu coba Hubungkan browser.', 503) from None
        self._contexts[provider_id] = context
        def closed(_):
            with self._lock:
                self._metadata[provider_id]['browser_open'] = False
        context.on('close', closed)
        with self._lock:
            self._metadata[provider_id]['browser_open'] = True
        return context

    def _start_driver(self):
        if self._driver is None:
            if self._factory:
                self._driver = self._factory()
            else:
                try:
                    from playwright.sync_api import sync_playwright
                except ImportError:
                    raise IntegrationError('Komponen browser belum terpasang. Pasang requirements-dashboard.txt dengan runtime proyek.', 503) from None
                self._driver = sync_playwright().start()

    def _goto(self, context, page, url, *, timeout=18000):
        _resolve_public(url)
        context.route('**/*', self._route_handler)
        try:
            return page.goto(url, wait_until='domcontentloaded', timeout=timeout)
        finally:
            # Sync Playwright does not dispatch Python routes while idle. Leaving a
            # route installed would stall requests when the user logs in manually.
            context.unroute('**/*', self._route_handler)

    def open(self, provider_id):
        return self._call(self._open, _provider_id(provider_id))

    def _open(self, provider_id):
        if self._login_running(provider_id):
            return self._snapshot_one(provider_id)
        context = self._contexts.get(provider_id)
        if context:
            context.close()
            self._contexts.pop(provider_id, None)
        profile = self._profile(provider_id)
        profile.mkdir(parents=True, exist_ok=True)
        try:
            process = self._login_launcher(profile, PORTALS[provider_id]['login'])
        except OSError:
            raise IntegrationError('Browser login tidak dapat dibuka. Tutup jendela profil portal yang tertinggal, lalu coba lagi.', 503) from None
        with self._lock:
            self._login_processes[provider_id] = process
            self._metadata[provider_id].update(browser_open=True, login_status='not_checked', login_checked_at=None,
                message='Login di jendela Edge biasa, lalu tutup jendela portal. Setelah itu klik Periksa akses; profil tetap tersimpan.')
            self._save()
        return self._snapshot_one(provider_id)

    def _login_status(self, page):
        host = urlsplit(page.url).hostname
        if host not in {portal['host'] for portal in PORTALS.values()}:
            return 'unknown'
        sign_in = re.compile(r'^(?:sign in|log in|login|masuk)$', re.I)
        sign_out = re.compile(r'^(?:sign out|log out|logout|keluar)$', re.I)
        incoming = page.get_by_role('link', name=sign_in).or_(page.get_by_role('button', name=sign_in))
        # Jobstreet mounts Sign out inside the collapsed account menu.
        outgoing = page.get_by_role('link', name=sign_out, include_hidden=True).or_(
            page.get_by_role('button', name=sign_out, include_hidden=True))
        indicators = incoming.or_(outgoing)
        avatar = page.locator('a[href="/id/profile"]:has(img)') if host == 'glints.com' else None
        if avatar is not None:
            indicators = indicators.or_(avatar)
        try:
            indicators.first.wait_for(state='attached', timeout=5000)
        except BrowserError:
            return 'unknown'
        signed_in = bool(outgoing.count()) or bool(avatar is not None and avatar.first.is_visible())
        signed_out = incoming.first.is_visible()
        if signed_in == signed_out:
            return 'unknown'
        return 'signed_in' if signed_in else 'signed_out'

    def _verify_jobs(self, context, jobs, checked, deadline):
        try:
            from .dashboard_jobs import apply_portal_detail
        except ImportError:
            from dashboard_jobs import apply_portal_detail
        if not jobs:
            return
        detail_page = context.new_page()
        closed_name = re.compile(r'^(?:this job (?:is no longer (?:advertised|available)|has expired)|(?:lowongan|pekerjaan) ini (?:sudah|telah) (?:ditutup|berakhir|kedaluwarsa))[\s.!]*$', re.I)
        try:
            for job in jobs:
                if job['status'] in ('closed', 'expired'):
                    continue
                detail = {'url': job['sourceUrl'], 'http_status': None}
                remaining = deadline - time.monotonic()
                if remaining > 1:
                    try:
                        response = self._goto(context, detail_page, job['sourceUrl'], timeout=min(4000, int(remaining * 1000)))
                        detail.update(url=detail_page.url, http_status=response.status if response else None)
                        if response and 200 <= response.status < 300:
                            apply_name = re.compile(r'^(?:apply(?: now| for this job| for ' + re.escape(job['title']) +
                                r'(?: at .+)?)?|lamar(?: sekarang| pekerjaan ini| & chat di aplikasi)?|kirim lamaran)$', re.I)
                            apply = detail_page.get_by_role('link', name=apply_name).or_(detail_page.get_by_role('button', name=apply_name))
                            closed = detail_page.get_by_text(closed_name)
                            try:
                                apply.or_(closed).first.wait_for(state='visible', timeout=max(1, min(1500, int((deadline-time.monotonic())*1000))))
                            except BrowserError:
                                pass  # Public metadata can establish status even without an application control.
                            headings = detail_page.get_by_role('heading', level=1).all_text_contents()
                            title = ' '.join(job['title'].split()).casefold()
                            detail.update(html=detail_page.content(),
                                          title_matches=any(' '.join(value.split()).casefold() == title for value in headings),
                                          can_apply=apply.first.is_visible() and apply.first.is_enabled())
                            if closed.first.is_visible():
                                detail['closed_status'] = 'expired' if re.search(r'expired|kedaluwarsa|berakhir', closed.first.inner_text(), re.I) else 'closed'
                    except (BrowserError, FetchError, OSError):
                        detail = {'url': job['sourceUrl'], 'http_status': None}
                apply_portal_detail(job, detail, checked)
        finally:
            detail_page.close()

    def _read(self, provider_id, query, filters, checked):
        deadline = time.monotonic() + 40
        try:
            from .dashboard_jobs import portal_search_url, parse_glints_page, parse_jobstreet_cards, _matches
        except ImportError:
            from dashboard_jobs import portal_search_url, parse_glints_page, parse_jobstreet_cards, _matches
        url = portal_search_url(provider_id, query, filters)
        if provider_id == 'glints' and not self._profile(provider_id).is_dir():
            response = _fetch(url, accept='text/html', check_robots=True)
            if response['status'] != 200:
                raise FetchError('action_required', 'Sumber meminta akses browser. Klik Hubungkan browser, lalu Cek koneksi.')
            content = decode_page(response['body'])
            return parse_glints_page(content, query, filters, checked), 'unknown'
        if not self._profile(provider_id).is_dir():
            raise FetchError('action_required', 'Hubungkan browser portal terlebih dahulu. Login dilakukan sendiri di situs resmi.')
        context = self._context(provider_id)
        page = context.pages[0] if context.pages else context.new_page()
        if any(urlsplit(tab.url).hostname in LOGIN_HOSTS for tab in context.pages if not tab.is_closed()):
            raise FetchError('action_required', 'Selesaikan atau tutup tab login di browser portal, lalu periksa lagi.')
        current = urlsplit(page.url)
        if current.hostname and (current.hostname != PORTALS[provider_id]['host'] or any(word in current.path.lower() for word in ('/oauth', '/login', '/signup', '/signin'))):
            raise FetchError('action_required', 'Selesaikan login di browser portal, lalu klik Cek koneksi lagi.')
        response = self._goto(context, page, url)
        if urlsplit(page.url).hostname != PORTALS[provider_id]['host']:
            raise FetchError('action_required', 'Portal meminta tindakan Anda. Selesaikan di browser, lalu periksa lagi.')
        if response and response.status in (401, 403, 429):
            raise FetchError('blocked', 'Portal membatasi akses. Periksa browser; hasil lama tetap disimpan.')
        if response is None or not 200 <= response.status < 300:
            raise FetchError('source_error', 'Halaman portal gagal dimuat. Hasil lama tetap disimpan.')
        if provider_id == 'glints':
            content = page.content()
            if len(content.encode('utf-8')) > 6 * 1024 * 1024:
                raise FetchError('too_large', 'Halaman portal melewati batas ukuran.')
            jobs = parse_glints_page(content, query, {**filters, 'status': 'all'}, checked)
        else:
            titles = page.locator('a[id^="job-title-"]')
            try:
                titles.first.wait_for(state='visible', timeout=7000)
            except BrowserError:
                headings = page.get_by_role('heading', level=1).all_text_contents()
                empty = any(re.match(r'^\s*(?:0\b.*\bjobs\b|no jobs found\b|tidak ada lowongan\b|tidak ditemukan lowongan\b)',
                                     heading, re.I) for heading in headings)
                if not empty:
                    raise FetchError('action_required', 'Kartu lowongan belum dapat dibaca. Periksa browser portal, lalu coba lagi.') from None
            cards = titles.evaluate_all('''elements => elements.slice(0,100).map(link => {
                const card=link.closest('article') || link.closest('[id^="jobcard-"]');
                if(!card)return null;
                const text=(selector)=>card.querySelector(selector)?.textContent?.trim()||'';
                return {id:link.id.replace('job-title-',''), title:link.textContent.trim(), url:link.href,
                    company:text('[data-automation="jobCompany"]'),location:text('[data-automation="jobLocation"]'),
                    salary:text('[data-automation="jobSalary"]'),employment:text('[data-automation="jobWorkType"]'),
                    description:card.innerText.slice(0,5000)};
            }).filter(Boolean)''')
            jobs = parse_jobstreet_cards(cards, query, {**filters, 'status': 'all'}, checked)
        login = self._login_status(page)
        self._verify_jobs(context, jobs, checked, deadline)
        return [job for job in jobs if _matches(job, query, filters)], login

    def read_portal(self, definition, query, filters, checked):
        if definition['id'] in ('google', 'google-careers'):
            try:
                batch = self._call(self._read_google, definition)
            except (IntegrationError, BrowserError, OSError):
                raise FetchError('source_error', 'Pencarian Google belum dapat dibaca. Sumber lain tetap diproses.') from None
            with ThreadPoolExecutor(max_workers=4) as workers:
                jobs = list(workers.map(lambda row: _discover_web_job(row, query, checked), batch.pop('cards')))
            return {**batch, 'jobs': [job for job in jobs if job and _matches(job, query, filters)]}
        provider_id = _provider_id(definition['id'])
        try:
            return self._call(self._read_batch_and_record, definition, query, filters, checked)
        except IntegrationError as error:
            raise FetchError('source_error', str(error)) from None

    def _read_google(self, definition):
        base = definition['search_url']
        url = _google_next_url(base, definition['cursor']) if definition.get('cursor') else base
        context = self._contexts.get('google')
        if context is None or not any(not page.is_closed() for page in context.pages):
            self._start_driver()
            path = self.root / 'search-browser'
            if _reparse(path) or path.resolve().parent != self.root:
                raise IntegrationError('Lokasi browser pencarian tidak aman.')
            context = self._driver.chromium.launch_persistent_context(str(path), channel='msedge', headless=False,
                                                                     timeout=15000, accept_downloads=False)
            self._contexts['google'] = context
        page = context.pages[0] if context.pages else context.new_page()
        response = self._goto(context, page, url)
        if (not response or response.status != 200 or urlsplit(page.url).path.startswith('/sorry')
                or urlsplit(page.url).hostname not in ('www.google.com', 'google.com')):
            raise FetchError('action_required', 'Google membatasi pencarian atau meminta tindakan di browser. Hasil portal lain tetap tersedia.')
        links = page.locator('#rso a:has(h3)')
        try:
            links.first.wait_for(state='visible', timeout=7000)
        except BrowserError:
            no_results = page.get_by_text(re.compile(r'(did not match any documents|tidak cocok dengan dokumen|tidak ditemukan hasil)', re.I))
            if not no_results.count():
                raise FetchError('action_required', 'Hasil Google belum dapat dibaca. Periksa jendela browser; CAPTCHA tidak diatasi otomatis.') from None
        cards = links.evaluate_all('''elements => elements.map(a => ({url:a.href,
            title:a.querySelector('h3').textContent.trim(),
            description:(a.closest('[data-hveid]')?.innerText || a.textContent).slice(0,3000)
        })).filter(row=>row.url.startsWith('https://')&&!new URL(row.url).hostname.endsWith('google.com'))''')
        cards = list({row['url']: row for row in cards}.values())
        if len(cards) > 100:
            raise FetchError('too_large', 'Halaman Google memuat terlalu banyak tautan untuk satu permintaan.')
        next_link = page.locator('a#pnnext')
        next_url = _google_next_url(base, next_link.get_attribute('href')) if next_link.count() else None
        return {'cards': cards, 'next_cursor': next_url, 'fetched_count': len(cards)}

    def _read_batch_and_record(self, definition, query, filters, checked):
        provider_id = _provider_id(definition['id'])
        try:
            batch, login = self._read_batch(provider_id, query, filters, checked, definition.get('cursor'))
        except (FetchError, IntegrationError, BrowserError, OSError) as error:
            message = str(error) if isinstance(error, (FetchError, IntegrationError)) else 'Halaman portal belum dapat dibaca. Coba lanjutkan kembali.'
            with self._lock:
                self._metadata[provider_id].update(access_status='error', message=message, checked_at=checked,
                    last_query=query, result_count=None, login_status='unknown', login_checked_at=checked)
                self._save()
            raise FetchError(getattr(error, 'code', 'source_error'), message) from None
        with self._lock:
            self._metadata[provider_id].update(access_status='action_required' if batch.get('warning') else 'ok',
                message=batch.get('warning') or f"{len(batch['jobs'])} hasil pada halaman yang baru dibaca.",
                checked_at=checked, last_query=query, result_count=len(batch['jobs']), login_status=login, login_checked_at=checked)
            self._save()
        return batch

    def _read_batch(self, provider_id, query, filters, checked, cursor):
        try:
            from .dashboard_jobs import portal_search_url, parse_jobstreet_cards
        except ImportError:
            from dashboard_jobs import portal_search_url, parse_jobstreet_cards
        base = portal_search_url(provider_id, query, filters)
        if provider_id == 'glints':
            portal_query = ('magang ' if filters.get('employment') == 'Magang' and 'magang' not in query.lower() else '') + query
            base = 'https://glints.com/id/opportunities/jobs/explore?' + urlencode({
                'keyword': portal_query, 'country': 'ID', 'locationName': 'All Cities/Provinces', 'lowestLocationLevel': '1'})
            offset = _scroll_offset(base, cursor)
            url = base
        else:
            url = _portal_next_url(base, cursor) if cursor else base
        context = self._context(provider_id)
        page = context.pages[0] if context.pages else context.new_page()
        if any(urlsplit(tab.url).hostname in LOGIN_HOSTS for tab in context.pages if not tab.is_closed()):
            raise FetchError('action_required', 'Selesaikan login di jendela portal dahulu.')
        continuing_glints = provider_id == 'glints' and cursor and self._glints_search == base and page.url == base
        if not continuing_glints:
            response = self._goto(context, page, url)
            if not response or response.status != 200 or urlsplit(page.url).hostname != PORTALS[provider_id]['host']:
                raise FetchError('blocked', 'Portal membatasi akses atau gagal dimuat. Hasil sebelumnya tetap tersedia.')
            if provider_id == 'glints':
                self._glints_search = base
        login = self._login_status(page)
        if provider_id == 'glints':
            titles = page.locator('a[aria-label^="Job card title:"]')
            try:
                titles.first.wait_for(state='visible', timeout=7000)
            except BrowserError:
                if not page.get_by_text(re.compile(r'(tidak ada lowongan|tidak ditemukan lowongan)', re.I)).count():
                    raise FetchError('source_error', 'Kartu Glints belum dapat dibaca.') from None
            deadline = time.monotonic() + 30
            while cursor and titles.count() <= offset:
                if page.get_by_role('button', name='Login Sekarang', exact=True).is_visible():
                    raise FetchError('action_required', 'Glints meminta login untuk hasil berikutnya. Periksa sesi di Integrasi.')
                previous = titles.count()
                page.keyboard.press('End')
                try:
                    page.wait_for_function('count => document.querySelectorAll(\'a[aria-label^="Job card title:"]\').length > count',
                                           arg=previous, timeout=min(7000, max(1, int((deadline-time.monotonic())*1000))))
                except BrowserError:
                    if page.locator('#id-homepage-footer').count() and not page.get_by_text('Memuat lebih banyak lowongan', exact=True).count():
                        return {'jobs': [], 'next_cursor': None, 'fetched_count': 0}, login
                    raise FetchError('source_error', 'Glints belum selesai memuat hasil berikutnya. Lanjutkan kembali; hasil yang terkumpul tetap tersimpan.') from None
                if time.monotonic() >= deadline:
                    raise FetchError('source_error', 'Glints sedang memulihkan posisi pencarian. Lanjutkan kembali untuk meneruskan.')
            batch = titles.evaluate_all('''(elements, offset) => ({total:elements.length, cards:elements.slice(offset).map(link => {
                const card=link.closest('[role="article"]');if(!card)return null;
                return {url:link.href,title:link.textContent.trim(),
                    company:card.querySelector('a[aria-label^="Job card company name:"]')?.textContent?.trim()||'',
                    location:Array.from(card.querySelectorAll('a[href*="/job-location/"]')).map(a=>a.textContent.trim()).join(', '),
                    employment:card.getAttribute('data-gtm-job-type'),sector:card.getAttribute('data-gtm-job-category'),
                    description:card.innerText.slice(0,5000)};
                }).filter(Boolean)})''', offset)
            cards, total = batch['cards'], batch['total']
            if len(cards) > 5000:
                raise FetchError('too_large', 'Halaman Glints terlalu besar. Persempit pencarian untuk melanjutkan.')
            jobs = _glints_card_jobs(cards, query, filters, checked, base)
            needs_login = page.get_by_role('button', name='Login Sekarang', exact=True).is_visible()
            next_cursor = _scroll_cursor(base, total) if total else None
            if needs_login:
                return {'jobs': jobs, 'next_cursor': next_cursor, 'fetched_count': len(cards),
                        'pagination_limited': True, 'code': 'action_required',
                        'warning': 'Glints meminta login untuk hasil berikutnya. Periksa sesi di Integrasi, lalu coba sumber ini kembali.'}, login
        else:
            titles = page.locator('a[id^="job-title-"]')
            try:
                titles.first.wait_for(state='visible', timeout=7000)
            except BrowserError:
                if not any(re.match(r'^\s*(?:0\b.*\bjobs\b|no jobs found\b|tidak ada lowongan\b)', h, re.I)
                           for h in page.get_by_role('heading', level=1).all_text_contents()):
                    raise FetchError('source_error', 'Kartu Jobstreet belum dapat dibaca.') from None
            cards = titles.evaluate_all('''elements => elements.map(link=>{
                const card=link.closest('article')||link.closest('[id^="jobcard-"]');if(!card)return null;
                const text=s=>card.querySelector(s)?.textContent?.trim()||'';
                return {id:link.id.replace('job-title-',''),title:link.textContent.trim(),url:link.href,
                    company:text('[data-automation="jobCompany"]'),location:text('[data-automation="jobLocation"]'),
                    salary:text('[data-automation="jobSalary"]'),employment:text('[data-automation="jobWorkType"]'),
                    description:card.innerText.slice(0,5000)};
                }).filter(Boolean)''')
            jobs = parse_jobstreet_cards(cards, query, filters, checked, native_search=True)
            next_link = page.get_by_role('link', name=re.compile(r'^(Next|Berikutnya)$', re.I))
            next_cursor = _portal_next_url(base, next_link.first.get_attribute('href')) if next_link.count() else None
        return {'jobs': jobs, 'next_cursor': next_cursor, 'fetched_count': len(cards)}, login

    def _read_and_record(self, provider_id, query, filters, checked, syncing):
        try:
            jobs, login = self._read(provider_id, query, filters, checked)
        except (FetchError, IntegrationError, BrowserError, OSError) as error:
            if isinstance(error, FetchError):
                code, message = error.code, str(error)
            else:
                code, message = 'source_error', 'Browser tidak dapat membaca hasil. Periksa portal dan coba lagi.'
            status = 'blocked' if code in ('blocked', 'blocked_robots', 'robots_denied') else 'action_required' if code in ('action_required', 'login_required') else 'error'
            with self._lock:
                self._metadata[provider_id].update(access_status=status, message=message, checked_at=checked,
                                                   last_query=query, result_count=None, login_status='unknown', login_checked_at=checked)
                self._save()
            raise FetchError(code, message) from None
        with self._lock:
            row = self._metadata[provider_id]
            row.update(access_status='ok', login_status=login, login_checked_at=checked, checked_at=checked, result_count=len(jobs),
                       last_query=query, message=f'{len(jobs)} hasil cocok dari halaman pertama. Periksa sumber sebelum melamar.')
            if syncing:
                row['last_sync_at'] = checked
            self._save()
        return jobs

    def check(self, provider_id, query):
        provider_id = _provider_id(provider_id)
        query, filters, _ = _search_inputs(query, {}, [provider_id], [])
        try:
            self._call(self._read_and_record, provider_id, query, filters, _stamp(), False)
        except FetchError:
            pass  # The source failure is represented by access_status, never a successful empty search.
        return self._snapshot_one(provider_id)

    def sync(self, provider_id, query, filters=None):
        provider_id = _provider_id(provider_id)
        query, filters, definitions = _search_inputs(query, filters, [provider_id], [])
        checked = _stamp()
        definition = definitions[0]
        if definition.get('skip'):
            raise IntegrationError('Filter tidak mencakup sumber Indonesia ini. Sesuaikan negara atau sumber.')
        try:
            jobs = self._call(self._read_and_record, provider_id, query, filters, checked, True)
        except FetchError as error:
            return {'ok': False, 'error': str(error), 'provider': self._snapshot_one(provider_id)}
        scope = 'Halaman pertama portal dengan pemeriksaan detail. Gunakan Cari lowongan untuk meneruskan ke halaman berikutnya.'
        return {'ok': True, 'jobs': jobs, 'searched_at': checked, 'provider': self._snapshot_one(provider_id),
                'sources': [{'id': provider_id, 'name': PORTALS[provider_id]['name'], 'status': 'ok',
                             'count': len(jobs), 'checked_at': checked, 'scope': scope,
                             'message': f'{len(jobs)} kecocokan; {scope}',
                             'mode': PORTALS[provider_id]['mode']}]}

    def disconnect(self, provider_id, *, confirm=False):
        if confirm is not True:
            raise IntegrationError('Konfirmasi keluar dan hapus sesi browser portal terlebih dahulu.')
        return self._call(self._disconnect, _provider_id(provider_id))

    def _disconnect(self, provider_id):
        if self._login_running(provider_id):
            raise IntegrationError('Tutup jendela Edge login portal dahulu sebelum menghapus sesi.', 409)
        path = self._profile(provider_id)
        context = self._contexts.get(provider_id)
        if context:
            context.close()
            self._contexts.pop(provider_id, None)
        if path.exists():
            # Fixed provider ID, checked reparse points, and exact parent prevent deleting other profiles.
            shutil.rmtree(path)
        with self._lock:
            self._metadata[provider_id] = _default(provider_id)
            self._save()
        return self._snapshot_one(provider_id)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        def stop():
            failures = []
            for provider_id, context in list(self._contexts.items()):
                try:
                    context.close()
                except BrowserError:
                    failures.append(provider_id)
                else:
                    self._contexts.pop(provider_id, None)
            if self._driver:
                try:
                    self._driver.stop()
                except BrowserError:
                    failures.append('driver')
                else:
                    self._driver = None
                    self._contexts.clear()
            if failures:
                raise IntegrationError('Sebagian browser tidak menutup dengan normal. Profil dipertahankan; tutup jendela portal dan coba lagi.', 503)
        try:
            self._executor.submit(stop).result(timeout=55)
        except (IntegrationError, FutureTimeout):
            with self._lock:
                self._closed = False
            raise
        else:
            self._executor.shutdown(wait=False, cancel_futures=True)
