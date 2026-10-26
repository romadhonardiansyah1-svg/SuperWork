import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import dashboard_integrations as integrations
from tools.dashboard_jobs import FetchError


class DiscoveryTests(unittest.TestCase):
    def test_glints_login_gate_preserves_results_and_retry_cursor(self):
        row = {'url': 'https://glints.com/id/opportunities/jobs/admin/00000000-0000-0000-0000-000000000001',
               'title': 'Admin', 'company': 'Example'}
        page = Mock(url='about:blank')
        page.is_closed.return_value = False
        page.locator.return_value.evaluate_all.return_value = {'cards': [row], 'total': 1}
        page.get_by_role.return_value.is_visible.return_value = True
        context = Mock(pages=[page])
        def navigate(context, page, url):
            page.url = url
            return Mock(status=200)
        with tempfile.TemporaryDirectory() as directory:
            runtime = integrations.IntegrationRuntime(Path(directory))
            try:
                with patch.object(runtime, '_context', return_value=context), patch.object(runtime, '_goto', side_effect=navigate), patch.object(runtime, '_login_status', return_value='signed_out'):
                    batch, login = runtime._read_batch('glints', 'admin', {}, '2026-10-25T00:00:00Z', None)
                self.assertEqual(len(batch['jobs']), 1)
                self.assertEqual(login, 'signed_out')
                self.assertTrue(batch['pagination_limited'])
                self.assertEqual(batch['code'], 'action_required')
                self.assertIn('login', batch['warning'])
                self.assertEqual(integrations._scroll_offset(page.url, batch['next_cursor']), 1)
            finally:
                runtime.close()

    def test_glints_continuation_transfers_only_new_cards_beyond_five_thousand(self):
        base = 'https://glints.com/id/opportunities/jobs/explore?keyword=admin&country=ID&locationName=All+Cities%2FProvinces&lowestLocationLevel=1'
        row = {'url': 'https://glints.com/id/opportunities/jobs/admin/00000000-0000-0000-0000-000000005001', 'title': 'Admin'}
        page = Mock(url=base)
        page.is_closed.return_value = False
        page.locator.return_value.count.return_value = 5001
        page.locator.return_value.evaluate_all.return_value = {'cards': [row], 'total': 5001}
        page.get_by_role.return_value.is_visible.return_value = False
        with tempfile.TemporaryDirectory() as directory:
            runtime = integrations.IntegrationRuntime(Path(directory))
            runtime._glints_search = base
            try:
                with patch.object(runtime, '_context', return_value=Mock(pages=[page])), patch.object(runtime, '_login_status', return_value='signed_in'):
                    batch, _ = runtime._read_batch('glints', 'admin', {}, '2026-10-25T00:00:00Z', integrations._scroll_cursor(base, 5000))
                self.assertEqual(len(batch['jobs']), 1)
                self.assertEqual(batch['fetched_count'], 1)
                self.assertEqual(integrations._scroll_offset(base, batch['next_cursor']), 5001)
            finally:
                runtime.close()

    def test_invalid_portal_cursor_does_not_launch_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = integrations.IntegrationRuntime(Path(directory))
            try:
                with patch.object(runtime, '_context') as context:
                    for provider, cursor in [('jobstreet', 'https://evil.example/admin-jobs?page=2'), ('glints', 'scroll:other:30')]:
                        with self.subTest(provider=provider), self.assertRaises(FetchError):
                            runtime._read_batch(provider, 'admin', {}, '2026-10-25T00:00:00Z', cursor)
                    context.assert_not_called()
            finally:
                runtime.close()

    def test_jobstreet_continuation_keeps_query_and_rejects_foreign_targets(self):
        base = 'https://id.jobstreet.com/admin-jobs'
        self.assertEqual(integrations._portal_next_url(base, '/admin-jobs?page=2'), base + '?page=2')
        for value in ('https://evil.example/admin-jobs?page=2', '/finance-jobs?page=2',
                      '/admin-jobs?page=-1', '/admin-jobs?page=2&token=secret',
                      '/admin-jobs?page=2&page=3', 'http://id.jobstreet.com/admin-jobs?page=2'):
            with self.subTest(value=value), self.assertRaises(FetchError):
                integrations._portal_next_url(base, value)

    def test_google_next_removes_tracking_and_rejects_query_swap(self):
        base = 'https://www.google.com/search?q=admin+jobs'
        self.assertEqual(integrations._google_next_url(base, '/search?q=admin+jobs&start=10&ved=tracking'),
                         base + '&start=10')
        for value in ('/search?q=other&start=10', '/url?q=admin+jobs&start=10',
                      'https://127.0.0.1/search?q=admin+jobs&start=10', '/search?q=admin+jobs&start=NaN'):
            with self.subTest(value=value), self.assertRaises(FetchError):
                integrations._google_next_url(base, value)

    def test_glints_scroll_cursor_bound_to_query_and_survives_restart(self):
        url = 'https://glints.com/id/opportunities/jobs/explore?keyword=admin&country=ID'
        cursor = integrations._scroll_cursor(url, 60)
        self.assertEqual(integrations._scroll_offset(url, cursor), 60)
        self.assertEqual(integrations._scroll_offset(url, None), 0)
        for value in ('scroll:60', integrations._scroll_cursor(url + 'x', 60), cursor + 'x'):
            with self.subTest(value=value), self.assertRaises(FetchError):
                integrations._scroll_offset(url, value)

    def test_glints_dom_cards_keep_more_than_twenty_and_do_not_invent_open_status(self):
        rows = [{'url': f'https://glints.com/id/opportunities/jobs/admin/00000000-0000-0000-0000-{n:012d}',
                 'title': 'Admin', 'company': 'Example', 'location': 'Jakarta, DKI Jakarta',
                 'employment': 'full_time', 'description': 'Admin perusahaan', 'sector': 'Administration'}
                for n in range(1, 36)]
        jobs = integrations._glints_card_jobs(rows, 'administrasi', {}, '2026-10-25T00:00:00Z', 'https://glints.com/id/explore/admin')
        self.assertEqual(len(jobs), 35)
        self.assertTrue(all(job['status'] == 'unknown' for job in jobs))
        self.assertTrue(all(job['searchQuery'] == 'administrasi' for job in jobs))

    def test_web_search_link_is_not_claimed_to_be_a_confirmed_job(self):
        row = {'title': 'Admin careers', 'url': 'https://careers.example.com/admin', 'description': 'Join our team'}
        with patch.object(integrations, '_fetch', side_effect=FetchError('network_error', 'Unavailable')):
            job = integrations._discover_web_job(row, 'admin', '2026-10-25T00:00:00Z')
        self.assertEqual(job['kind'], 'web_result')
        self.assertEqual(job['status'], 'unknown')
        self.assertEqual(job['origin'], 'unknown')
        self.assertEqual(job['foundOn'], 'Google')
        self.assertEqual(job['sourceName'], 'careers.example.com')

    def test_web_job_metadata_preserves_employer_dates_and_intermediary_label(self):
        row = {'title': 'Admin vacancy', 'url': 'https://depokloker.com/lowongan/admin', 'description': 'Admin'}
        posting = {'@type': 'JobPosting', 'title': 'Staff Admin', 'description': 'Office administration',
                   'hiringOrganization': {'name': 'Example Ltd', 'sameAs': 'https://example.com'},
                   'validThrough': '2020-01-01', 'jobLocation': {'address': {'addressCountry': 'ID', 'addressLocality': 'Jakarta'}}}
        response = {'url': row['url'], 'status': 200, 'headers': {'content-type': 'text/html'},
                    'body': ('<script type="application/ld+json">' + json.dumps(posting) + '</script>').encode()}
        with patch.object(integrations, '_fetch', return_value=response):
            job = integrations._discover_web_job(row, 'admin', '2026-10-25T00:00:00Z')
        self.assertEqual(job['company'], 'Example Ltd')
        self.assertEqual(job['status'], 'expired')
        self.assertEqual(job['sourceType'], 'web')
        self.assertEqual(job['origin'], 'unknown')
        self.assertNotEqual(job.get('kind'), 'web_result')

    def test_web_discovery_does_not_follow_malicious_result_urls(self):
        with patch.object(integrations, '_fetch') as fetch:
            for url in ('http://127.0.0.1/secret', 'https://a.example/?token=secret', 'javascript:alert(1)'):
                self.assertIsNone(integrations._discover_web_job({'title': 'Admin', 'url': url}, 'admin', '2026-10-25T00:00:00Z'))
            fetch.assert_not_called()

    def test_unrelated_job_metadata_cannot_replace_search_result_or_its_status(self):
        row = {'title': 'Admin vacancy', 'url': 'https://careers.example.com/admin'}
        posting = {'@type': 'JobPosting', 'title': 'Engineer', 'url': '/engineer', 'validThrough': '2020-01-01'}
        response = {'url': row['url'], 'status': 200, 'headers': {'content-type': 'text/html'},
                    'body': ('<script type="application/ld+json">' + json.dumps(posting) + '</script>').encode()}
        with patch.object(integrations, '_fetch', return_value=response):
            job = integrations._discover_web_job(row, 'admin', '2026-10-25T00:00:00Z')
        self.assertEqual(job['kind'], 'web_result')
        self.assertEqual(job['title'], row['title'])
        self.assertEqual(job['status'], 'unknown')

    def test_malformed_employer_url_does_not_discard_valid_job_metadata(self):
        row = {'title': 'Admin vacancy', 'url': 'https://careers.example.com/admin'}
        posting = {'@type': 'JobPosting', 'title': 'Admin', 'url': '/admin',
                   'hiringOrganization': {'name': 'Example', 'sameAs': 'https://['}}
        response = {'url': row['url'], 'status': 200, 'headers': {'content-type': 'text/html'},
                    'body': ('<script type="application/ld+json">' + json.dumps(posting) + '</script>').encode()}
        with patch.object(integrations, '_fetch', return_value=response):
            job = integrations._discover_web_job(row, 'admin', '2026-10-25T00:00:00Z')
        self.assertEqual(job['title'], 'Admin')
        self.assertEqual(job['sourceType'], 'web')
        self.assertEqual(job['origin'], 'unknown')
