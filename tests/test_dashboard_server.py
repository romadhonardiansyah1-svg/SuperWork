import http.client
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest

from pypdf import PdfReader

from tools.dashboard_server import DashboardHTTPServer


class FakeRouter:
    base_url = 'http://127.0.0.1:20128/v1'
    def status(self):
        return {'status': 'stopped', 'managed': False, 'can_stop': False, 'base_url': self.base_url, 'message': 'Fixture'}
    def start(self):
        return {**self.status(), 'status': 'ready', 'managed': True, 'can_stop': True}
    def stop(self):
        return self.status()
    restart = start
    def logs(self):
        return ['Log fixture tanpa kredensial']


class DashboardServerTests(unittest.TestCase):
    def test_search_forwards_source_continuations(self):
        from unittest.mock import patch
        cursor = {'jobstreet': 'https://id.jobstreet.com/admin-jobs?page=2'}
        with patch('tools.dashboard_jobs.search_jobs', return_value={'ok': True, 'jobs': [], 'sources': []}) as search:
            result = self.request('POST', '/api/search', {'query': 'admin', 'sources': ['jobstreet'], 'cursors': cursor},
                                  {'X-SuperWork-Token': self.token, 'Origin': self.origin})
        self.assertEqual(result[0], 200)
        self.assertEqual(search.call_args.kwargs['cursors'], cursor)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'dashboard').mkdir()
        (root / 'dashboard' / 'index.html').write_text('<!doctype html><title>SuperWork fixture</title>')
        self.server = DashboardHTTPServer(('127.0.0.1', 0), root=root, router=FakeRouter(), models_provider=lambda: [{'id': 'fixture/model'}])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        self.token = self.request('GET', '/api/bootstrap')[1]['token']

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        connection.request(method, path, json.dumps(body) if body is not None else None, request_headers)
        response = connection.getresponse()
        raw = response.read()
        result = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
        status, returned_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, result, returned_headers

    def test_health_bootstrap_and_static_no_directory_leaks(self):
        self.assertEqual(self.request('GET', '/api/health')[1]['app'], 'SuperWork')
        bootstrap = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(bootstrap['models'], [{'id': 'fixture/model'}])
        self.assertEqual(bootstrap['state'], {})
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertEqual(self.request('GET', '/../CLAUDE.md')[0], 404)
        self.assertEqual(self.request('GET', '/.superwork/state.json')[0], 404)

    def test_mutation_requires_token_same_origin_and_json(self):
        body = {'state': {'profile': {'name': 'Uji API'}}}
        self.assertEqual(self.request('PUT', '/api/state', body)[0], 403)
        good_headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin,
                        'If-Match': '"' + hashlib.sha256(b'').hexdigest() + '"'}
        self.assertEqual(self.request('PUT', '/api/state', body, good_headers)[0], 200)
        self.assertEqual(self.request('PUT', '/api/state', body, {**good_headers, 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('PUT', '/api/state', body, {**good_headers, 'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['state']['profile']['name'], 'Uji API')

    def test_bootstrap_publishes_exact_state_revision(self):
        bootstrap = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(bootstrap.get('revision'), hashlib.sha256(b'').hexdigest())
        raw = b'{ "profile": {"name": "Fixture"} }\n'
        self.server.store.state_path.write_bytes(raw)
        bootstrap = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(bootstrap['state'], json.loads(raw))
        self.assertEqual(bootstrap['revision'], hashlib.sha256(raw).hexdigest())

    def test_state_save_requires_strict_quoted_revision(self):
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        body = {'state': {'profile': {'name': 'Tab lama'}}}
        for revision in (None, '', '*', 'a' * 64, '"' + 'a' * 63 + '"', '"' + 'g' * 64 + '"',
                         '"' + 'A' * 64 + '"', 'W/"' + 'a' * 64 + '"', '"' + 'a' * 64 + '", "' + 'b' * 64 + '"'):
            with self.subTest(revision=revision):
                request_headers = headers if revision is None else {**headers, 'If-Match': revision}
                status, result, _ = self.request('PUT', '/api/state', body, request_headers)
                self.assertEqual(status, 409)
                self.assertFalse(result['ok'])
                self.assertIn('tab', result['error'].lower())
                self.assertFalse(self.server.store.state_path.exists())
                self.assertEqual(list(self.server.store.files.iterdir()), [])

    def test_old_tab_cannot_replace_newer_state_or_backup(self):
        self.server.store.save_state({'profile': {'name': 'Awal'}})
        revision = hashlib.sha256(self.server.store.state_path.read_bytes()).hexdigest()
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin, 'If-Match': '"' + revision + '"'}
        state = {'profile': {'name': 'Terbaru'}}
        status, result, _ = self.request('PUT', '/api/state', {'state': state}, headers)
        self.assertEqual(status, 200)
        before = {path.name: path.read_bytes() for path in self.server.store.root.iterdir() if path.is_file()}
        stale_status, stale_result, _ = self.request('PUT', '/api/state', {'state': {'profile': {'name': 'Tab lama'}}}, headers)
        self.assertEqual(stale_status, 409)
        self.assertIn('tab', stale_result['error'].lower())
        self.assertEqual({path.name: path.read_bytes() for path in self.server.store.root.iterdir() if path.is_file()}, before)
        self.assertEqual(result['state'], state)
        self.assertEqual(result['revision'], hashlib.sha256(self.server.store.state_path.read_bytes()).hexdigest())
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['state'], state)

    def test_concurrent_http_writers_have_exactly_one_winner(self):
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin,
                   'If-Match': '"' + hashlib.sha256(b'').hexdigest() + '"'}
        barrier = threading.Barrier(2)
        outcomes = []
        def write(name):
            barrier.wait(timeout=5)
            outcomes.append(self.request('PUT', '/api/state', {'state': {'profile': {'name': name}}}, headers)[:2])
        threads = [threading.Thread(target=write, args=(name,)) for name in ('Tab A', 'Tab B')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual([status for status, _ in outcomes], [200, 409])
        winner = next(result for status, result in outcomes if status == 200)
        current = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(current['state'], winner['state'])
        self.assertEqual(current['revision'], winner['revision'])

    def test_restore_requires_revision_and_stale_restore_cannot_add_files(self):
        self.server.store.save_state({'profile': {'name': 'Awal'}})
        revision = hashlib.sha256(self.server.store.state_path.read_bytes()).hexdigest()
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        snapshot = {'format': 'superwork-backup', 'version': 1, 'state': {'profile': {'name': 'Cadangan'}},
                    'files': [{'metadata': {'id': 'a' * 32, 'name': 'CV-fixture.txt', 'size': 3, 'text': 'Uji'},
                               'content_base64': 'VWpp'}]}
        self.assertEqual(self.request('POST', '/api/restore', snapshot, headers)[0], 409)
        self.server.store.save_state({'profile': {'name': 'Terbaru'}})
        before = {str(path.relative_to(self.server.store.root)): path.read_bytes()
                  for path in self.server.store.root.rglob('*') if path.is_file()}
        status, result, _ = self.request('POST', '/api/restore', snapshot, {**headers, 'If-Match': '"' + revision + '"'})
        self.assertEqual(status, 409)
        self.assertIn('tab', result['error'].lower())
        self.assertEqual({str(path.relative_to(self.server.store.root)): path.read_bytes()
                          for path in self.server.store.root.rglob('*') if path.is_file()}, before)
        revision = hashlib.sha256(self.server.store.state_path.read_bytes()).hexdigest()
        status, result, _ = self.request('POST', '/api/restore', snapshot, {**headers, 'If-Match': '"' + revision + '"'})
        self.assertEqual(status, 200)
        self.assertEqual(result['state'], snapshot['state'])
        self.assertEqual(result['revision'], hashlib.sha256(self.server.store.state_path.read_bytes()).hexdigest())
        self.assertEqual(self.server.store.get_file('a' * 32)[1], b'Uji')

    def test_host_and_cross_site_requests_are_rejected(self):
        self.assertEqual(self.request('GET', '/api/bootstrap', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/bootstrap', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_models_are_loaded_each_request_not_static_fixture(self):
        self.server.models_provider = lambda: [{'id': 'added/new-model'}]
        self.assertEqual(self.request('GET', '/api/models')[1]['models'][0]['id'], 'added/new-model')

    def test_security_headers_and_unknown_commands(self):
        headers = self.request('GET', '/')[2]
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        result = self.request('POST', '/api/exec', {'command': 'whoami'}, {'X-SuperWork-Token': self.token, 'Origin': self.origin})
        self.assertEqual(result[0], 404)

    def test_router_stop_does_not_shutdown_dashboard(self):
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        self.assertEqual(self.request('POST', '/api/router/stop', {}, headers)[0], 200)
        self.assertTrue(self.request('GET', '/api/health')[1]['ok'])

    def test_new_mutations_are_rejected_when_shutdown_has_started(self):
        self.server.stopping = True
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        self.assertEqual(self.request('POST', '/api/router/start', {}, headers)[0], 409)
        self.assertEqual(self.request('POST', '/api/ai', {'model': 'fixture/model', 'task': 'chat'}, headers)[0], 409)
        self.assertTrue(self.request('GET', '/api/health')[1]['ok'])

    def test_saved_document_downloads_as_pdf_attachment(self):
        self.server.store.save_state({'documents': [{'id': 101, 'name': 'CV Rani QA', 'kind': 'cv',
            'generated': {'title': 'CV Rani QA', 'content': 'Rani QA\n\nKeahlian: HTML, CSS, Python',
                          'review_notes': ['NOT_FOR_APPLICATION']}}]})
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        status, raw, returned = self.request('POST', '/api/documents/pdf', {'id': 101}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(returned['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', returned['Content-Disposition'])
        self.assertTrue(returned['Content-Disposition'].endswith('.pdf'))
        self.assertTrue(raw.startswith(b'%PDF-'))
        text = ''.join(page.extract_text() for page in PdfReader(BytesIO(raw)).pages)
        self.assertIn('Rani QA', text)
        self.assertNotIn('NOT_FOR_APPLICATION', text)

    def test_pdf_endpoint_rejects_missing_unknown_or_non_integer_ids_and_extra_fields(self):
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        for body in ({}, {'id': True}, {'id': 0}, {'id': -1}, {'id': '101'}, {'id': 2**53},
                     {'id': 101, 'html': '<img src="file:///C:/private.txt">'}, {'id': 101, 'path': '../private.txt'}):
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/api/documents/pdf', body, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/documents/pdf', {'id': 999}, headers)[0], 404)

    def test_pdf_download_requires_session_and_same_origin(self):
        self.assertEqual(self.request('POST', '/api/documents/pdf', {'id': 101})[0], 403)
        self.assertEqual(self.request('POST', '/api/documents/pdf', {'id': 101},
                         {'X-SuperWork-Token': self.token, 'Origin': 'https://evil.example'})[0], 403)

    def test_legacy_document_has_clear_json_error_instead_of_blank_pdf(self):
        self.server.store.save_state({'documents': [{'id': 101, 'name': 'Legacy CV'}]})
        status, data, returned = self.request('POST', '/api/documents/pdf', {'id': 101},
                         {'X-SuperWork-Token': self.token, 'Origin': self.origin})
        self.assertEqual(status, 400)
        self.assertFalse(data['ok'])
        self.assertIn('Isi dokumen belum tersedia', data['error'])
        self.assertIn('Buat ulang dokumen', data['error'])
        self.assertNotIn('draf', data['error'].lower())
        self.assertTrue(returned['Content-Type'].startswith('application/json'))
        self.assertNotIn('Content-Disposition', returned)

    def test_bootstrap_publishes_canonical_job_sources_and_feedback_task(self):
        from tools.dashboard_jobs import SOURCE_DEFINITIONS
        bootstrap = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(bootstrap['job_sources'], SOURCE_DEFINITIONS)
        self.assertIn('feedback', bootstrap['capabilities']['ai_tasks'])

    def test_stale_delete_after_another_tab_save_cannot_remove_referenced_cv(self):
        file = self.server.store.add_file('CV-tab-uji.txt', 'VWpp')
        initial = {'cv': {'id': file['id'], 'key': file['id']}, 'profile': {'name': 'Awal'}}
        self.server.store.save_state(initial)
        revision = self.request('GET', '/api/bootstrap')[1]['revision']
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin, 'If-Match': '"' + revision + '"'}
        newer = {**initial, 'profile': {'name': 'Tab B'}}
        self.assertEqual(self.request('PUT', '/api/state', {'state': newer}, headers)[0], 200)
        before = {str(path.relative_to(self.server.store.root)): path.read_bytes()
                  for path in self.server.store.root.rglob('*') if path.is_file()}
        status, result, _ = self.request('DELETE', '/api/files/' + file['id'], {}, headers)
        self.assertEqual(status, 409)
        self.assertIn('tab', result['error'].lower())
        self.assertEqual(self.server.store.load_state(), newer)
        self.assertEqual(self.server.store.get_file(file['id'])[1], b'Uji')
        self.assertEqual({str(path.relative_to(self.server.store.root)): path.read_bytes()
                          for path in self.server.store.root.rglob('*') if path.is_file()}, before)

    def test_file_delete_requires_valid_quoted_revision(self):
        file = self.server.store.add_file('CV-precondition.txt', 'VWpp')
        self.server.store.save_state({'cv': {'id': file['id']}})
        before = self.server.store.snapshot()
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin}
        for revision in (None, before['revision'], '*', '"bad"'):
            with self.subTest(revision=revision):
                request_headers = headers if revision is None else {**headers, 'If-Match': revision}
                self.assertEqual(self.request('DELETE', '/api/files/' + file['id'], {}, request_headers)[0], 409)
                self.assertEqual(self.server.store.snapshot(), before)
                self.assertEqual(self.server.store.get_file(file['id'])[1], b'Uji')

    def test_file_delete_cleans_references_and_returns_atomic_state_revision(self):
        file = self.server.store.add_file('CV-aktif-delete.txt', 'VWpp')
        initial = {'cv': {'id': file['id']}, 'cvHistory': [{'key': file['id']}, {'id': 'b' * 32}],
                   'pendingProfile': {'name': 'Impor'}, 'confirmed': True,
                   'profile': {'name': 'Tetap'}, 'apps': [{'id': 1}], 'documents': [{'id': 2}]}
        self.server.store.save_state(initial)
        revision = self.request('GET', '/api/bootstrap')[1]['revision']
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin, 'If-Match': '"' + revision + '"'}
        status, result, _ = self.request('DELETE', '/api/files/' + file['id'], {}, headers)
        self.assertEqual(status, 200)
        expected = {**initial, 'cv': None, 'cvHistory': [{'id': 'b' * 32}], 'pendingProfile': None, 'confirmed': False}
        self.assertEqual(result.get('state'), expected)
        self.assertEqual(result['revision'], hashlib.sha256(self.server.store.state_path.read_bytes()).hexdigest())
        self.assertEqual(self.server.store.load_state(), expected)
        self.assertNotEqual(result['revision'], revision)
        self.assertEqual(list(self.server.store.files.iterdir()), [])

    def test_concurrent_delete_and_save_share_one_revision_guard(self):
        file = self.server.store.add_file('CV-delete-concurrent.txt', 'VWpp')
        initial = {'cv': {'id': file['id']}, 'confirmed': True, 'profile': {'name': 'Awal'}}
        self.server.store.save_state(initial)
        revision = self.request('GET', '/api/bootstrap')[1]['revision']
        headers = {'X-SuperWork-Token': self.token, 'Origin': self.origin, 'If-Match': '"' + revision + '"'}
        barrier = threading.Barrier(2)
        outcomes = []
        def mutate(method, path, body):
            barrier.wait(timeout=5)
            outcomes.append((method, self.request(method, path, body, headers)[:2]))
        newer = {**initial, 'profile': {'name': 'Tab B'}}
        threads = [threading.Thread(target=mutate, args=('DELETE', '/api/files/' + file['id'], {})),
                   threading.Thread(target=mutate, args=('PUT', '/api/state', {'state': newer}))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual([status for _, (status, _) in outcomes], [200, 409])
        current = self.request('GET', '/api/bootstrap')[1]
        winner, (_, response) = next(item for item in outcomes if item[1][0] == 200)
        self.assertEqual(current['state'], response['state'])
        self.assertEqual(current['revision'], response['revision'])
        if winner == 'DELETE':
            self.assertIsNone(current['state']['cv'])
            self.assertEqual(list(self.server.store.files.iterdir()), [])
        else:
            self.assertEqual(current['state'], newer)
            self.assertEqual(self.server.store.get_file(file['id'])[1], b'Uji')


if __name__ == '__main__':
    unittest.main()
