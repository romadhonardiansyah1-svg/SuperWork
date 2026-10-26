import json
import http.client
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tools.dashboard_integrations import BrowserError, IntegrationError, IntegrationRuntime
from tools.dashboard_jobs import FetchError, parse_glints_page, parse_jobstreet_cards
from tools.dashboard_server import DashboardHTTPServer


class FakePage:
    url = 'about:blank'
    def goto(self, url, **kwargs):
        self.url = url
        return type('Response', (), {'status': getattr(self, 'response_status', 200)})()
    def is_closed(self):
        return False
    def bring_to_front(self):
        pass


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]
        self.closed = False
        self.intercepting = False
    def close(self):
        self.closed = True
    def on(self, *args):
        pass
    def route(self, *args):
        self.intercepting = True
    def unroute(self, *args):
        self.intercepting = False


class FakePlaywright:
    def __init__(self):
        self.chromium = self
        self.calls = []
        self.contexts = []
    def launch_persistent_context(self, path, **kwargs):
        self.calls.append((str(path), kwargs, threading.get_ident()))
        context = FakeContext()
        self.contexts.append(context)
        return context
    def stop(self):
        pass


class FakeLoginProcess:
    returncode = None
    def poll(self):
        return self.returncode


class LoginIndicator:
    def __init__(self, entries=()):
        self.entries = list(entries)
    @property
    def first(self):
        return LoginIndicator(self.entries[:1])
    def is_visible(self):
        return bool(self.entries and self.entries[0])
    def is_enabled(self):
        return self.is_visible()
    def count(self):
        return len(self.entries)
    def or_(self, other):
        return LoginIndicator(self.entries + other.entries)
    def wait_for(self, **kwargs):
        if not self.entries:
            raise BrowserError('No account indicator')


class LoginIndicatorPage:
    def __init__(self, url, controls=(), avatar=False):
        self.url, self.controls, self.avatar = url, controls, avatar
    def get_by_role(self, role, name, exact=False, include_hidden=False):
        return LoginIndicator([visible for kind, text, visible in self.controls
                               if kind == role and (include_hidden or visible)
                               and (bool(name.search(text)) if hasattr(name, 'search') else text == name)])
    def locator(self, selector):
        return LoginIndicator([True] if self.avatar and '/id/profile' in selector else [])


class LoginDetectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.runtime = IntegrationRuntime(self.root)
        self.addCleanup(self.runtime.close)

    def test_glints_profile_avatar_is_positive_account_evidence(self):
        page = LoginIndicatorPage('https://glints.com/id/explore/admin', avatar=True)
        self.assertEqual(self.runtime._login_status(page), 'signed_in')

    def test_glints_uppercase_sign_in_is_signed_out(self):
        page = LoginIndicatorPage('https://glints.com/id/explore/admin', [('button', 'MASUK', True)])
        self.assertEqual(self.runtime._login_status(page), 'signed_out')

    def test_jobstreet_hidden_sign_out_menu_is_positive_account_evidence(self):
        page = LoginIndicatorPage('https://id.jobstreet.com/admin-jobs', [('link', 'Sign out', False)])
        self.assertEqual(self.runtime._login_status(page), 'signed_in')

    def test_sign_in_and_unrecognized_pages_are_not_claimed_signed_in(self):
        for page, expected in [
                (LoginIndicatorPage('https://id.jobstreet.com/admin-jobs', [('link', 'Sign in', True)]), 'signed_out'),
                (LoginIndicatorPage('https://id.jobstreet.com/admin-jobs'), 'unknown'),
                (LoginIndicatorPage('https://evil.example/', [('link', 'Sign out', True)], avatar=True), 'unknown')]:
            with self.subTest(url=page.url, expected=expected):
                self.assertEqual(self.runtime._login_status(page), expected)

    def test_conflicting_indicators_are_not_claimed_signed_in(self):
        page = LoginIndicatorPage('https://id.jobstreet.com/admin-jobs',
                                  [('link', 'Sign in', True), ('link', 'Sign out', False)])
        self.assertEqual(self.runtime._login_status(page), 'unknown')

    def test_verified_login_and_check_time_survive_restart(self):
        (self.root / 'portal-browser/glints').mkdir(parents=True)
        with patch.object(self.runtime, '_read', return_value=([], 'signed_in')):
            first = self.runtime.check('glints', 'admin')
        self.runtime.close()
        restarted = IntegrationRuntime(self.root)
        self.addCleanup(restarted.close)
        second = next(row for row in restarted.snapshot() if row['id'] == 'glints')
        self.assertEqual(second['login_status'], 'signed_in')
        self.assertEqual(second['login_checked_at'], first['checked_at'])

    def test_failed_recheck_does_not_leave_a_false_signed_in_status(self):
        with patch.object(self.runtime, '_read', return_value=([], 'signed_in')):
            self.runtime.check('glints', 'admin')
        with patch.object(self.runtime, '_read', side_effect=FetchError('blocked', 'Source blocked')):
            result = self.runtime.check('glints', 'admin')
        self.assertEqual(result['login_status'], 'unknown')


class ManualLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.driver = FakePlaywright()
        self.launches = []
        def launch(path, url):
            process = FakeLoginProcess()
            self.launches.append((path, url, process))
            return process
        self.runtime = IntegrationRuntime(self.root, browser_factory=lambda: self.driver, login_launcher=launch)
        self.addCleanup(self.runtime.close)

    def test_login_opens_native_browser_without_a_playwright_context(self):
        for provider, url in [('glints', 'https://glints.com/id/login'),
                              ('jobstreet', 'https://id.jobstreet.com/id/oauth/login?returnUrl=%2F')]:
            row = self.runtime.open(provider)
            self.assertTrue(row['browser_open'])
            self.assertIs(row.get('login_browser_open'), True)
            self.assertTrue(row['session_saved'])
            self.assertNotEqual(row['login_status'], 'signed_in')
            self.assertEqual(self.launches[-1][:2], (self.root / 'portal-browser' / provider, url))
        self.assertEqual(self.driver.calls, [])

    def test_repeated_open_keeps_manual_login_and_sso_untouched(self):
        self.runtime.open('glints')
        self.runtime.open('glints')
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.driver.calls, [])

    def test_reader_and_disconnect_wait_until_the_user_closes_manual_login(self):
        self.runtime.open('glints')
        with self.assertRaisesRegex(FetchError, 'Tutup|tutup'):
            self.runtime._call(self.runtime._context, 'glints')
        with self.assertRaisesRegex(IntegrationError, 'Tutup|tutup'):
            self.runtime.disconnect('glints', confirm=True)
        self.assertTrue((self.root / 'portal-browser/glints').is_dir())
        self.assertEqual(self.driver.calls, [])

    def test_check_reports_manual_login_pending_without_reading_the_page(self):
        self.runtime.open('glints')
        row = self.runtime.check('glints', 'magang')
        self.assertEqual(row['access_status'], 'action_required')
        self.assertIsNone(row['result_count'])
        self.assertEqual(self.driver.calls, [])

    def test_failed_reader_close_does_not_launch_native_browser_over_its_profile(self):
        context = self.runtime._call(self.runtime._context, 'glints')
        with patch.object(context, 'close', side_effect=BrowserError('fixture failure')):
            with self.assertRaises(BrowserError):
                self.runtime.open('glints')
        self.assertEqual(self.launches, [])
        self.assertIs(self.runtime._contexts['glints'], context)

    @unittest.skipUnless(os.name == 'nt', 'Windows native browser launcher')
    def test_native_command_has_no_automation_or_debugging_channel(self):
        from tools.dashboard_integrations import _launch_login_browser
        program_files = self.root / 'program-files'
        executable = program_files / 'Microsoft/Edge/Application/msedge.exe'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'fixture-not-executed')
        with patch.dict(os.environ, {'PROGRAMFILES(X86)': str(program_files)}), \
             patch('tools.dashboard_integrations.subprocess.Popen', return_value=FakeLoginProcess()) as launch:
            _launch_login_browser(self.root / 'portal-browser/glints', 'https://glints.com/id/login')
        command = launch.call_args.args[0]
        self.assertEqual(command[0], str(executable))
        self.assertIn('--user-data-dir=' + str(self.root / 'portal-browser/glints'), command)
        self.assertFalse(any('remote-debugging' in arg or 'enable-automation' in arg or 'disable-blink' in arg for arg in command))
        self.assertNotIn('shell', launch.call_args.kwargs)

    def test_finished_login_hands_the_same_profile_to_reader_without_copying(self):
        self.runtime.open('glints')
        profile = self.launches[0][0]
        marker = profile / 'native-fixture-marker'
        marker.write_text('keep', encoding='utf-8')
        self.launches[0][2].returncode = 0
        self.runtime._call(self.runtime._context, 'glints')
        self.assertEqual(self.driver.calls[0][0], str(profile))
        self.assertEqual(marker.read_text(), 'keep')

    def test_application_close_does_not_terminate_a_manual_login(self):
        self.runtime.open('glints')
        process = self.launches[0][2]
        self.runtime.close()
        self.assertIsNone(process.poll())


class IntegrationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.driver = FakePlaywright()
        resolver = patch('tools.dashboard_integrations._resolve_public', return_value=['8.8.8.8'])
        resolver.start()
        self.addCleanup(resolver.stop)
        self.login_processes = {}
        def launch(path, url):
            process = FakeLoginProcess()
            self.login_processes[path.name] = process
            return process
        self.runtime = IntegrationRuntime(self.root, browser_factory=lambda: self.driver, login_launcher=launch)
        self.addCleanup(self.runtime.close)

    def open_reader(self, provider):
        if provider not in self.runtime._contexts:
            self.runtime.open(provider)
            self.login_processes[provider].returncode = 0
        return self.runtime._call(self.runtime._context, provider)

    def test_default_metadata_does_not_claim_login_or_access(self):
        providers = self.runtime.snapshot()
        self.assertEqual({p['id'] for p in providers}, {'glints', 'jobstreet'})
        for provider in providers:
            self.assertEqual(provider['access_status'], 'not_checked')
            self.assertEqual(provider['login_status'], 'not_checked')
            self.assertIsNone(provider['checked_at'])
            self.assertFalse(provider['session_saved'])
        self.assertEqual(self.driver.calls, [])

    def test_browser_launch_isolated_and_owned_by_one_worker_thread(self):
        for provider in ('glints', 'jobstreet', 'glints'):
            self.open_reader(provider)
            result = next(row for row in self.runtime.snapshot() if row['id'] == provider)
            self.assertTrue(result['browser_open'])
            self.assertIs(result.get('login_browser_open'), False)
            self.assertEqual(result['access_status'], 'not_checked')
            self.assertNotEqual(result['login_status'], 'signed_in')
        self.assertEqual(len(self.driver.calls), 2)
        self.assertEqual(len({call[2] for call in self.driver.calls}), 1)
        self.assertNotEqual(self.driver.calls[0][2], threading.get_ident())
        for path, options, _ in self.driver.calls:
            self.assertEqual(Path(path).parent, self.root / 'portal-browser')
            self.assertEqual(options['channel'], 'msedge')
            self.assertFalse(options['headless'])
            self.assertNotIn('ignore_https_errors', options)
            self.assertNotIn('proxy', options)

    def test_unknown_provider_never_starts_browser(self):
        for provider in ('../glints', 'gmail', 'https://evil.test', '', None):
            with self.subTest(provider=provider), self.assertRaises(IntegrationError):
                self.runtime.open(provider)
        self.assertEqual(self.driver.calls, [])

    def test_closed_browser_profile_is_retained_and_not_called_logged_in(self):
        self.open_reader('glints')
        self.runtime.close()
        second = IntegrationRuntime(self.root, browser_factory=lambda: self.driver)
        self.addCleanup(second.close)
        provider = next(p for p in second.snapshot() if p['id'] == 'glints')
        self.assertTrue(provider['session_saved'])
        self.assertFalse(provider['browser_open'])
        self.assertNotEqual(provider['login_status'], 'signed_in')

    def test_disconnect_requires_confirmation_and_only_clears_own_profile(self):
        unrelated = self.root / 'state.json'
        unrelated.write_text('{"profile":{"name":"keep"}}', encoding='utf-8')
        self.open_reader('glints')
        self.open_reader('jobstreet')
        with self.assertRaises(IntegrationError):
            self.runtime.disconnect('glints', confirm=False)
        self.assertTrue((self.root / 'portal-browser/glints').is_dir())
        result = self.runtime.disconnect('glints', confirm=True)
        self.assertFalse(result['session_saved'])
        self.assertFalse((self.root / 'portal-browser/glints').exists())
        self.assertTrue((self.root / 'portal-browser/jobstreet').is_dir())
        self.assertEqual(json.loads(unrelated.read_text())['profile']['name'], 'keep')

    def test_private_profile_is_not_copied_to_metadata(self):
        self.runtime.open('glints')
        private = self.root / 'portal-browser/glints/Private session'
        private.write_text('private-session-fixture', encoding='utf-8')
        rendered = json.dumps(self.runtime.snapshot())
        persisted = (self.root / 'portal-integrations.json').read_text(encoding='utf-8')
        for value in (rendered, persisted):
            self.assertNotIn('private-session-fixture', value)
            self.assertNotIn(str(self.root), value)

    def test_reader_browser_has_no_idle_request_interceptor(self):
        self.open_reader('glints')
        self.assertFalse(self.driver.contexts[0].intercepting,
                         'Manual login requests must not wait for an idle Python sync dispatcher.')

    def test_failed_disconnect_does_not_lose_browser_before_retry(self):
        self.open_reader('glints')
        context = self.driver.contexts[0]
        with patch.object(context, 'close', side_effect=BrowserError('fixture close failure')):
            with self.assertRaises(BrowserError):
                self.runtime.disconnect('glints', confirm=True)
            with self.assertRaises(BrowserError):
                self.runtime.disconnect('glints', confirm=True)
        self.assertTrue((self.root / 'portal-browser/glints').is_dir())
        self.runtime.disconnect('glints', confirm=True)
        self.assertTrue(context.closed)

    def test_close_attempts_other_browser_and_driver_after_one_failure(self):
        self.open_reader('glints')
        self.open_reader('jobstreet')
        with patch.object(self.driver.contexts[0], 'close', side_effect=BrowserError('fixture failure')):
            with patch.object(self.driver, 'stop') as stop:
                with self.assertRaises(IntegrationError):
                    self.runtime.close()
                stop.assert_called_once()
        self.assertTrue(self.driver.contexts[1].closed)
        self.runtime.close()

    def test_browser_http_failure_cannot_be_successful_empty_search(self):
        self.open_reader('jobstreet')
        page = self.driver.contexts[0].pages[0]
        page.url = 'https://id.jobstreet.com/'
        page.response_status = 500
        result = self.runtime.sync('jobstreet', 'magang', {})
        self.assertFalse(result['ok'])
        self.assertEqual(result['provider']['access_status'], 'error')
        self.assertIsNone(result['provider']['result_count'])

    def test_detail_recognizes_portal_application_labels_without_accepting_another_job(self):
        checked, title = '2026-10-25T10:00:00Z', 'Admin (Administrasi Operasional)'
        cases = [('jobstreet', 'link', 'Apply for Admin (Administrasi Operasional) at Kusuma Candra Apartment', 'open'),
                 ('glints', 'button', 'LAMAR & CHAT DI APLIKASI', 'open'),
                 ('jobstreet', 'link', 'Apply for Warehouse Assistant at Kusuma Candra Apartment', 'unknown')]
        for provider, role, label, expected in cases:
            with self.subTest(provider=provider, label=label):
                if provider == 'glints':
                    source_id = '650bedb6-c639-412d-a444-a85bf64b4265'
                    url = 'https://glints.com/id/opportunities/jobs/admin/' + source_id
                    data = {'props': {'apolloCache': {'Job:' + source_id: {'id': source_id, 'title': title, 'status': 'PENDING'}}}}
                    source = '<a href="' + url + '">Admin</a><script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
                    jobs = parse_glints_page(source, 'admin', {'status': 'all'}, checked)
                else:
                    url = 'https://id.jobstreet.com/job/95456856'
                    jobs = parse_jobstreet_cards([{'id': '95456856', 'title': title, 'url': url}],
                                                'admin', {'status': 'all'}, checked)
                context, page = Mock(), Mock()
                context.new_page.return_value = page
                page.url = url
                indicator = LoginIndicatorPage(url, controls=[(role, label, True)])
                headings = Mock()
                headings.all_text_contents.return_value = [title]
                page.get_by_role.side_effect = lambda kind, **kwargs: headings if kind == 'heading' else indicator.get_by_role(kind, **kwargs)
                page.get_by_text.return_value = LoginIndicator()
                page.content.return_value = ''
                with patch('tools.dashboard_integrations.time.monotonic', return_value=0), \
                     patch.object(self.runtime, '_goto', return_value=Mock(status=200)):
                    self.runtime._verify_jobs(context, jobs, checked, deadline=10)
                self.assertEqual((jobs[0]['status'], jobs[0]['evidence']['status']), (expected, expected))

    def test_detail_budget_exhausted_never_navigates_or_claims_open(self):
        checked = '2026-10-25T10:00:00Z'
        jobs = parse_jobstreet_cards([{'id': '1', 'title': 'Admin', 'url': '/job/1', 'status': 'OPEN'}],
                                    'admin', {'status': 'all'}, checked)
        context, page = Mock(), Mock()
        context.new_page.return_value = page
        with patch('tools.dashboard_integrations.time.monotonic', return_value=50), \
             patch.object(self.runtime, '_goto') as navigate:
            self.runtime._verify_jobs(context, jobs, checked, deadline=49)
        navigate.assert_not_called()
        self.assertEqual((jobs[0]['status'], jobs[0]['evidence']['status']), ('unknown', 'unknown'))
        self.assertIsNone(jobs[0]['evidence']['http_status'])
        page.close.assert_called_once()

    def test_detail_navigation_failure_is_unknown_and_closes_the_page(self):
        checked = '2026-10-25T10:00:00Z'
        for error in (BrowserError('timeout'), FetchError('blocked', 'Portal membatasi akses.'), OSError('network')):
            with self.subTest(error=type(error).__name__):
                jobs = parse_jobstreet_cards([{'id': '1', 'title': 'Admin', 'url': '/job/1', 'status': 'OPEN'}],
                                            'admin', {'status': 'all'}, checked)
                context, page = Mock(), Mock()
                context.new_page.return_value = page
                with patch('tools.dashboard_integrations.time.monotonic', return_value=0), \
                     patch.object(self.runtime, '_goto', side_effect=error):
                    self.runtime._verify_jobs(context, jobs, checked, deadline=10)
                self.assertEqual((jobs[0]['status'], jobs[0]['evidence']['status']), ('unknown', 'unknown'))
                self.assertIsNone(jobs[0]['evidence']['http_status'])
                page.close.assert_called_once()

    def test_detail_batch_skips_closed_jobs_and_reuses_one_page_with_fresh_evidence(self):
        previous, checked = '2026-10-24T10:00:00Z', '2026-10-25T10:00:00Z'
        jobs = parse_jobstreet_cards([{'id': str(index), 'title': 'Admin', 'url': f'/job/{index}', 'status': status}
                                     for index, status in enumerate(('CLOSED', 'EXPIRED', 'OPEN', 'PENDING'), 1)],
                                    'admin', {'status': 'all'}, previous)
        context, page, controls, closed, headings = Mock(), Mock(), Mock(), Mock(), Mock()
        context.new_page.return_value = page
        controls.first = controls
        controls.or_.return_value = controls
        controls.is_visible.return_value = controls.is_enabled.return_value = True
        closed.first = closed
        closed.is_visible.return_value = False
        headings.all_text_contents.return_value = ['Admin']
        page.get_by_role.side_effect = lambda role, **kwargs: headings if role == 'heading' else controls
        page.get_by_text.return_value = closed
        page.content.return_value = ''
        def navigate(context, detail_page, url, **kwargs):
            detail_page.url = url
            return Mock(status=404 if url.endswith('/3') else 200)
        with patch('tools.dashboard_integrations.time.monotonic', return_value=0), \
             patch.object(self.runtime, '_goto', side_effect=navigate) as navigation:
            self.runtime._verify_jobs(context, jobs, checked, deadline=10)
        self.assertEqual([job['status'] for job in jobs], ['closed', 'expired', 'closed', 'open'])
        self.assertEqual([job['checkedAt'] for job in jobs], [previous, previous, checked, checked])
        self.assertEqual([call.args[2] for call in navigation.call_args_list], [jobs[2]['sourceUrl'], jobs[3]['sourceUrl']])
        self.assertTrue(all(call.args[:2] == (context, page) for call in navigation.call_args_list))
        context.new_page.assert_called_once()
        page.close.assert_called_once()

    def test_sync_source_summary_is_readable_by_dashboard(self):
        for count in (0, 2):
            with self.subTest(count=count), patch.object(self.runtime, '_read_and_record', return_value=[{}] * count):
                source = self.runtime.sync('glints', 'magang', {})['sources'][0]
            self.assertEqual(source['message'], f'{count} kecocokan; {source["scope"]}')

    def test_missing_cards_with_twenty_jobs_is_not_empty_search(self):
        self.open_reader('jobstreet')
        page = self.driver.contexts[0].pages[0]
        page.url = 'https://id.jobstreet.com/'
        titles, body, headings = Mock(), Mock(), Mock()
        titles.first.wait_for.side_effect = BrowserError('no card selector')
        titles.evaluate_all.return_value = []
        body.inner_text.return_value = '20 jobs in Indonesia'
        headings.all_text_contents.return_value = ['20 jobs in Indonesia']
        with patch.object(page, 'locator', create=True, side_effect=lambda value: body if value == 'body' else titles), \
             patch.object(page, 'get_by_role', create=True, return_value=headings), \
             patch.object(self.runtime, '_login_status', return_value='unknown'):
            result = self.runtime.sync('jobstreet', 'magang', {})
        self.assertFalse(result['ok'])
        self.assertEqual(result['provider']['access_status'], 'action_required')

    def test_failed_source_preserves_sync_time_and_reports_unknown_count(self):
        self.runtime._metadata['glints'].update(last_sync_at='2026-10-24T00:00:00Z', result_count=2)
        with patch.object(self.runtime, '_read', side_effect=__import__('tools.dashboard_jobs', fromlist=['FetchError']).FetchError('blocked', 'Portal membatasi akses.')):
            provider = self.runtime.check('glints', 'magang')
        self.assertEqual(provider['access_status'], 'blocked')
        self.assertEqual(provider['last_sync_at'], '2026-10-24T00:00:00Z')
        self.assertIsNone(provider['result_count'])
        self.assertIsNotNone(provider['checked_at'])


class FakeIntegrations:
    def __init__(self):
        self.calls = []
    def snapshot(self):
        return [{'id': 'glints', 'name': 'Glints', 'access_status': 'not_checked'}]
    def open(self, provider):
        self.calls.append(('open', provider))
        return {'id': provider, 'browser_open': True, 'login_status': 'not_checked'}
    def check(self, provider, query):
        return {'id': provider, 'access_status': 'blocked', 'result_count': None}
    def sync(self, provider, query, filters):
        self.calls.append(('sync', provider, query, filters))
        return {'ok': True, 'jobs': [], 'sources': [], 'searched_at': '2026-10-25T00:00:00Z', 'provider': {'id': provider}}
    def disconnect(self, provider, *, confirm):
        self.calls.append(('disconnect', provider))
        return {'id': provider, 'session_saved': False}
    def read_portal(self, *args):
        return []
    def close(self):
        pass


class IntegrationAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.integration = FakeIntegrations()
        self.server = DashboardHTTPServer(('127.0.0.1', 0), root=self.temp.name, integrations=self.integration,
                                          models_provider=lambda: [])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        self.headers = {'X-SuperWork-Token': self.server.token, 'Origin': self.origin}

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, None if body is None else json.dumps(body),
                           {'Content-Type': 'application/json', **(headers or {})})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_read_metadata_does_not_start_browser_or_expose_profile(self):
        status, result = self.request('GET', '/api/integrations')
        self.assertEqual(status, 200)
        self.assertEqual(result['providers'][0]['id'], 'glints')
        self.assertEqual(self.integration.calls, [])

    def test_browser_actions_require_token_and_same_origin(self):
        path = '/api/integrations/glints/open'
        self.assertEqual(self.request('POST', path, {})[0], 403)
        self.assertEqual(self.request('POST', path, {}, {**self.headers, 'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.integration.calls, [])
        self.assertEqual(self.request('POST', path, {}, self.headers)[0], 200)
        self.assertEqual(self.integration.calls, [('open', 'glints')])

    def test_unknown_fields_provider_and_missing_confirmation_are_rejected(self):
        for action, body in [('open', {'url': 'http://127.0.0.1/private'}), ('disconnect', {}),
                             ('disconnect', {'confirm': 1}), ('check', {'query': 'x', 'password': 'never-store'}),
                             ('sync', {'query': 'x', 'filters': {'bad': 'field'}})]:
            with self.subTest(action=action, body=body):
                self.assertEqual(self.request('POST', '/api/integrations/glints/'+action, body, self.headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/integrations/notion/open', {}, self.headers)[0], 400)
        self.assertEqual(self.integration.calls, [])

    def test_check_failure_is_explicit_and_does_not_change_candidate_state(self):
        original = self.server.store.snapshot()
        status, result = self.request('POST', '/api/integrations/glints/check', {'query': 'magang'}, self.headers)
        self.assertEqual(status, 200)
        self.assertEqual(result['provider']['access_status'], 'blocked')
        self.assertIsNone(result['provider']['result_count'])
        self.assertEqual(original, self.server.store.snapshot())

    def test_sync_validates_input_and_preserves_store(self):
        for query in ('', 'x'*201, 123):
            self.assertEqual(self.request('POST', '/api/integrations/glints/sync', {'query': query}, self.headers)[0], 400)
        before = self.server.store.snapshot()
        status, result = self.request('POST', '/api/integrations/glints/sync', {'query': 'magang', 'filters': {'countries': []}}, self.headers)
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertEqual(self.server.store.snapshot(), before)
        self.assertEqual(self.integration.calls, [('sync', 'glints', 'magang', {'countries': []})])


if __name__ == '__main__':
    unittest.main()
