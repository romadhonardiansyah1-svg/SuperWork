"""Local dashboard search boundaries; no provider calls in the regression suite."""

import json
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tools import dashboard_jobs as jobs


def response(body, status=200, content_type="application/json", url="https://example.com/jobs"):
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    return {"url": url, "status": status, "headers": {"content-type": content_type}, "body": raw}


class SearchTests(unittest.TestCase):
    def kalibrr(self, **changes):
        row = {'id': 123, 'name': 'Staf Administrasi', 'slug': 'staf-administrasi',
               'company': {'code': 'contoh', 'name': 'Perusahaan Contoh', 'verifiedBusiness': True},
               'googleLocation': {'addressComponents': {'city': 'Bandung', 'region': 'Jawa Barat', 'country': 'Indonesia'}},
               'description': 'Administrasi laporan keuangan.', 'qualifications': 'Microsoft Excel',
               'tenure': 'Full time', 'workExperience': 200, 'function': 'Administration',
               'visibility': 'public', 'applicationEndDate': '2099-12-31T17:00:00+00:00',
               'activationDate': '2026-10-24T08:00:00+00:00'}
        row.update(changes)
        return row

    def kalibrr_page(self, rows):
        data = json.dumps({'props': {'pageProps': {'jobs': rows}}})
        return response('<script type="application/json" id="__NEXT_DATA__">'+data+'</script>', content_type='text/html')

    def test_indonesia_sources_registered_and_manual_portals_do_not_fetch(self):
        self.assertTrue({'kalibrr', 'jobstreet', 'glints'}.issubset(jobs.DEFAULT_SOURCES))
        with patch.object(jobs, '_fetch') as fetch, patch.object(jobs, '_fetch_json') as fetch_json:
            result = jobs.search_jobs('administrasi', {'employment': 'Magang'}, sources=['jobstreet', 'glints'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['jobs'], [])
        self.assertEqual([s['status'] for s in result['sources']], ['manual', 'manual'])
        for source in result['sources']:
            self.assertIsNone(source['count'])
            self.assertIsNone(source['checked_at'])
            self.assertTrue(source['search_url'].startswith('https://'))
            self.assertIn('magang', source['search_url'])
        fetch.assert_not_called()
        fetch_json.assert_not_called()

    def test_kalibrr_non_it_keeps_source_deadline_location_and_unknown_identity(self):
        with patch.object(jobs, '_fetch', return_value=self.kalibrr_page([self.kalibrr(tenure='Contractual')])) as fetch:
            result = jobs.search_jobs('administrasi', sources=['kalibrr'])
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['jobs']), 1)
        job = result['jobs'][0]
        self.assertEqual((job['sourceName'], job['foundOn'], job['country'], job['region'], job['city']),
                         ('Kalibrr', 'Kalibrr', 'Indonesia', 'Jawa Barat', 'Bandung'))
        self.assertEqual((job['status'], job['origin']), ('unknown', 'unknown'))
        self.assertEqual(job['employment'], 'Kontrak')
        self.assertEqual(job['deadline'], '2099-12-31T17:00:00+00:00')
        self.assertEqual(job['checkedAt'], result['searched_at'])
        self.assertEqual(job['evidence']['method'], 'public_page')
        self.assertIn('/c/contoh/jobs/123/staf-administrasi', job['sourceUrl'])
        self.assertTrue(fetch.call_args.kwargs['check_robots'])

    def test_kalibrr_encodes_keywords_as_one_path_segment(self):
        with patch.object(jobs, '_fetch', return_value=self.kalibrr_page([])) as fetch:
            jobs.search_jobs('admin/../../login?x=1', sources=['kalibrr'])
        self.assertEqual(fetch.call_args.args[0], 'https://www.kalibrr.id/id-ID/home/te/admin%2F..%2F..%2Flogin%3Fx%3D1')

    def test_kalibrr_supports_internship_from_level_or_explicit_title(self):
        rows = [self.kalibrr(name='Admin Operasional', workExperience=100),
                self.kalibrr(id=124, name='Admin Magang', slug='admin-magang'),
                self.kalibrr(id=125, name='Internal Admin', slug='internal-admin')]
        with patch.object(jobs, '_fetch', return_value=self.kalibrr_page(rows)):
            result = jobs.search_jobs('admin', {'employment': 'Magang'}, sources=['kalibrr'])
        self.assertEqual([j['sourceId'] for j in result['jobs']], ['123', '124'])
        self.assertTrue(all(j['employment'] == 'Magang' for j in result['jobs']))

    def test_kalibrr_expired_postings_hidden_from_active_but_available_in_archive(self):
        rows = [self.kalibrr(applicationEndDate='2020-01-01T00:00:00Z')]
        with patch.object(jobs, '_fetch', return_value=self.kalibrr_page(rows)):
            active = jobs.search_jobs('admin', sources=['kalibrr'])
            archive = jobs.search_jobs('admin', {'status': 'all'}, sources=['kalibrr'])
        self.assertEqual(active['jobs'], [])
        self.assertEqual(archive['jobs'][0]['status'], 'expired')
        self.assertEqual(archive['jobs'][0]['evidence']['status'], 'expired')

    def test_kalibrr_uses_optional_filters_and_skips_nonpublic_or_invalid_rows(self):
        rows = [self.kalibrr(), self.kalibrr(id=124, visibility='private'),
                self.kalibrr(id=125, company={'code': '../evil', 'name': 'Bad'}),
                self.kalibrr(id=True), self.kalibrr(id=126, slug='admin-lain', workExperience={})]
        with patch.object(jobs, '_fetch', return_value=self.kalibrr_page(rows)):
            selected = jobs.search_jobs('admin', {'countries':['Indonesia'], 'region':'Jawa Barat', 'city':'Bandung'}, sources=['kalibrr'])
            foreign = jobs.search_jobs('admin', {'countries':['Jerman']}, sources=['kalibrr'])
        self.assertEqual(len(selected['jobs']), 2)
        self.assertEqual(foreign['jobs'], [])

    def test_kalibrr_blocked_and_changed_pages_report_failure_without_fabrication(self):
        for page in [response('blocked', status=403, content_type='text/html'), response('<html>login</html>', content_type='text/html'),
                     response('<script id="__NEXT_DATA__">not-json</script>', content_type='text/html')]:
            with self.subTest(page=page['status']), patch.object(jobs, '_fetch', return_value=page):
                result = jobs.search_jobs('admin', sources=['kalibrr'])
            self.assertEqual(result['jobs'], [])
            self.assertEqual(result['sources'][0]['status'], 'error')

    def test_manual_portals_respect_non_indonesia_country_filter(self):
        result = jobs.search_jobs('marketing', {'countries':['Jerman']}, sources=['jobstreet', 'glints'])
        self.assertTrue(all(s['status'] == 'skipped' and not s.get('search_url') for s in result['sources']))

    def test_manual_portals_respect_source_name_and_type_filters(self):
        result = jobs.search_jobs('admin', {'sourceName':'Jobstreet'}, sources=['jobstreet', 'glints'])
        self.assertEqual([s['status'] for s in result['sources']], ['manual', 'skipped'])
        result = jobs.search_jobs('admin', {'sourceType':'company'}, sources=['jobstreet', 'glints'])
        self.assertTrue(all(s['status'] == 'skipped' for s in result['sources']))

    def freehire(self, **changes):
        row = {"public_slug": "acme-front", "title": "Front-end Engineer", "company": "Acme",
               "url": "https://careers.example.com/jobs/123", "countries": ["ID"],
               "cities": ["Bandung"], "skills": ["React"], "work_mode": "remote",
               "description": "Build interfaces", "posted_at": "2026-01-01T12:00:00Z"}
        row.update(changes)
        return row

    def test_global_search_has_no_mandatory_country(self):
        data = {"data": [self.freehire(), self.freehire(public_slug="other", company="Other",
                    url="https://careers.example.com/jobs/456", countries=["DE"])]}
        with patch.object(jobs, "_fetch_json", return_value=data) as fetch:
            result = jobs.search_jobs("front end engineer", sources=["freehire"])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["jobs"]), 2)
        self.assertNotIn("countries=", fetch.call_args.args[0])
        self.assertTrue(all(j["status"] == "unknown" and not j["mock"] for j in result["jobs"]))
        self.assertEqual(result["jobs"][0]["eligible"], [])

    def test_optional_countries_filter_and_stable_id(self):
        with patch.object(jobs, "_fetch_json", return_value={"data": [self.freehire()]}):
            first = jobs.search_jobs("frontend", {"countries": ["Indonesia"]}, ["freehire"])
            again = jobs.search_jobs("frontend", {}, ["freehire"])
            missing = jobs.search_jobs("frontend", {"countries": ["Jerman"]}, ["freehire"])
        self.assertEqual(first["jobs"][0]["id"], again["jobs"][0]["id"])
        self.assertIsInstance(first["jobs"][0]["id"], int)
        self.assertLess(first["jobs"][0]["id"], 2 ** 53)
        self.assertEqual(missing["jobs"], [])

    def test_query_is_encoded_and_never_an_api_path(self):
        with patch.object(jobs, "_fetch_json", return_value={"data": []}) as fetch:
            jobs.search_jobs("a&countries=RU", sources=["freehire"])
        self.assertIn("q=a%26countries%3DRU", fetch.call_args.args[0])

    def test_failure_of_one_source_keeps_other_source_results(self):
        def fake(url):
            if "freehire" in url:
                raise jobs.FetchError("network_error", "Sumber tidak dapat dihubungi.")
            return {"data": [{"slug": "finance", "title": "Finance Manager", "company_name": "A",
                              "url": "https://www.arbeitnow.com/view/finance", "location": "Berlin"}]}
        with patch.object(jobs, "_fetch_json", side_effect=fake):
            result = jobs.search_jobs("manager", sources=["freehire", "arbeitnow"])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual([s["status"] for s in result["sources"]], ["error", "ok"])

    def test_general_corpus_is_not_limited_to_engineering(self):
        data = {"data": [{"slug": "nurse", "title": "Nurse", "company_name": "Clinic",
                 "url": "https://www.arbeitnow.com/view/nurse", "location": "Berlin",
                 "description": "Nursing", "remote": False}]}
        with patch.object(jobs, "_fetch_json", return_value=data):
            result = jobs.search_jobs("nurse", sources=["arbeitnow"])
        self.assertEqual(result["jobs"][0]["title"], "Nurse")
        self.assertIn("terbaru", result["sources"][0]["message"])

    def test_user_boards_support_greenhouse_and_lever_without_fixed_employers(self):
        def fake(url):
            if "greenhouse" in url:
                return {"jobs": [{"id": 4, "title": "Accountant", "absolute_url": "https://boards.greenhouse.io/customco/jobs/4", "content": "Accounts", "location": {"name": "Toronto"}}]}
            return [{"id": "abc", "text": "Accountant", "hostedUrl": "https://jobs.lever.co/customco/abc",
                     "country": "CA", "categories": {"location": "Toronto", "team": "Finance"}}]
        with patch.object(jobs, "_fetch_json", side_effect=fake):
            result = jobs.search_jobs("accountant", sources=[], boards=[
                {"type": "greenhouse", "slug": "customco"}, {"type": "lever", "slug": "customco"}])
        self.assertEqual(len(result["jobs"]), 2)
        self.assertTrue(all(j["sourceType"] == "company" for j in result["jobs"]))
        self.assertTrue(all(j["origin"] == "unknown" for j in result["jobs"]))

    def test_deduplicate_same_source_url_with_tracking_but_not_distinct_posts(self):
        data = {"data": [self.freehire(), self.freehire(url="https://careers.example.com/jobs/123?utm_source=x"),
                         self.freehire(url="https://careers.example.com/jobs/124")]}
        with patch.object(jobs, "_fetch_json", return_value=data):
            result = jobs.search_jobs("frontend", sources=["freehire"])
        self.assertEqual(len(result["jobs"]), 2)

    def test_source_page_results_are_not_silently_truncated_to_twenty(self):
        data = {"data": [self.freehire(url=f"https://example.com/jobs/{i}", public_slug=str(i)) for i in range(35)]}
        with patch.object(jobs, "_fetch_json", return_value=data):
            result = jobs.search_jobs("frontend", sources=["freehire"])
        self.assertEqual(len(result["jobs"]), 35)
        self.assertEqual(result['sources'][0]['fetched_count'], 35)
        for args in [("x" * 201,), ("",), ("x", {}, ["invalid"]),
                     ("x", {"countries": "ID"}), ("x", {}, [], [{"type": "lever", "slug": "../../a"}])]:
            self.assertFalse(jobs.search_jobs(*args)["ok"])

    def test_discard_unsafe_job_urls_and_malformed_items(self):
        data = {"data": [None, {}, self.freehire(url="javascript:alert(1)"), self.freehire(url="http://127.0.0.1/admin"), self.freehire()]}
        with patch.object(jobs, "_fetch_json", return_value=data):
            result = jobs.search_jobs("frontend", sources=["freehire"])
        self.assertEqual(len(result["jobs"]), 1)

    def test_recency_unknowns_and_expired_filter(self):
        with patch.object(jobs, "_fetch_json", return_value={"data": [self.freehire(posted_at=None)]}):
            result = jobs.search_jobs("frontend", {"recency": "7"}, ["freehire"])
            open_only = jobs.search_jobs("frontend", {"status": "open"}, ["freehire"])
        self.assertEqual(result["jobs"], [])
        self.assertEqual(open_only["jobs"], [])

    def test_bad_source_shape_is_an_error_instead_of_empty_success(self):
        with patch.object(jobs, "_fetch_json", return_value={"unexpected": []}):
            result = jobs.search_jobs("frontend", sources=["freehire"])
        self.assertEqual(result["sources"][0]["status"], "error")

    def test_magang_query_uses_official_facet_and_retains_other_keywords(self):
        row = self.freehire(title="Frontend Engineering Intern", enrichment={"employment_type": "internship"})
        with patch.object(jobs, "_fetch_json", return_value={"data": [row]}) as fetch:
            result = jobs.search_jobs("magang frontend", {"employment": "Magang"}, ["freehire"])
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params.get("employment_type"), ["internship"])
        self.assertEqual(params.get("q"), ["frontend"])
        self.assertNotIn("countries", params)
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["employment"], "Magang")

    def test_all_magang_aliases_match_english_role_and_structured_type(self):
        row = self.freehire(title="Software Engineer", enrichment={"employment_type": "INTERNSHIP"})
        for query in ("intern", "internship", "magang", "Praktikum"):
            with self.subTest(query=query), patch.object(jobs, "_fetch_json", return_value={"data": [row]}) as fetch:
                result = jobs.search_jobs(query, {"employment": "Magang"}, ["freehire"])
                params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
                self.assertEqual(params.get("employment_type"), ["internship"])
                self.assertNotIn("q", params)
                self.assertEqual(len(result["jobs"]), 1)

    def test_internship_filter_cannot_turn_all_provider_results_into_internships(self):
        rows = [self.freehire(title="International Sales Manager", description="We support our internship program."),
                self.freehire(title="Internal Auditor", description="Our team mentors interns.", url="https://example.com/audit"),
                self.freehire(title="Frontend Intern", description="Build software", url="https://example.com/intern"),
                self.freehire(title="Internship Program Manager", description="Coordinate hiring", url="https://example.com/program-manager")]
        with patch.object(jobs, "_fetch_json", return_value={"data": rows}):
            result = jobs.search_jobs("internship", {"employment": "Magang"}, ["freehire"])
        self.assertEqual([row["title"] for row in result["jobs"]], ["Frontend Intern"])

    def test_internship_type_still_requires_original_role_keyword(self):
        rows = [self.freehire(title="Frontend Intern"),
                self.freehire(title="Accounting Intern", url="https://example.com/accounting", skills=[])]
        with patch.object(jobs, "_fetch_json", return_value={"data": rows}):
            result = jobs.search_jobs("frontend", {"employment": "Magang"}, ["freehire"])
        self.assertEqual([row["title"] for row in result["jobs"]], ["Frontend Intern"])

    def test_freehire_structured_type_and_clear_title_are_both_supported(self):
        rows = [self.freehire(title="Software Engineer", employment_type="internship"),
                self.freehire(title="Praktikum Softwareentwicklung", url="https://example.com/praktikum"),
                self.freehire(title="Software Engineer Intern", url="https://example.com/fulltime-intern", enrichment={"employment_type": "Full Time"})]
        with patch.object(jobs, "_fetch_json", return_value={"data": rows}):
            result = jobs.search_jobs("internship", {"employment": "Magang"}, ["freehire"])
        self.assertEqual(len(result["jobs"]), 3)
        self.assertTrue(all(row["employment"] == "Magang" for row in result["jobs"]))

    def test_arbeitnow_uses_actual_type_anywhere_in_list_instead_of_level(self):
        rows = [{"slug": str(index), "title": title, "company_name": "A", "job_types": types,
                 "url": f"https://www.arbeitnow.com/view/{index}"}
                for index, (title, types) in enumerate([
                    ("Operations", ["Manager", "Full Time"]),
                    ("Operations", ["Entry", "Praktikum"]),
                    ("Operations", ["Experienced", "Part-Time"]),
                    ("Operations", ["Mid", "Contract"]),
                    ("Operations", ["Manager", "Experienced"]),
                    ("Operations", ["Full Time", "Internship"]),
                ])]
        with patch.object(jobs, "_fetch_json", return_value={"data": rows}):
            result = jobs.search_jobs("operations", sources=["arbeitnow"])
            filtered = jobs.search_jobs("operations", {"employment": "Magang"}, ["arbeitnow"])
        self.assertEqual([row["employment"] for row in result["jobs"]], [
            "Penuh waktu", "Magang", "Paruh waktu", "Kontrak", jobs.UNKNOWN, "Magang"])
        self.assertEqual(len(filtered["jobs"]), 2)

    def test_arbeitnow_magang_alias_matches_without_description_inference(self):
        rows = [{"slug": str(index), "title": title, "company_name": "A", "job_types": types,
                 "description": "We also have an internship program.", "url": f"https://www.arbeitnow.com/view/{index}"}
                for index, (title, types) in enumerate([
                    ("Finance Praktikum", ["Entry"]),
                    ("Finance", ["Intern"]),
                    ("Finance Manager", ["Manager", "Full Time"]),
                ])]
        with patch.object(jobs, "_fetch_json", return_value={"data": rows}):
            result = jobs.search_jobs("magang finance", {"employment": "Magang"}, ["arbeitnow"])
        self.assertEqual([row["title"] for row in result["jobs"]], ["Finance Praktikum", "Finance"])

    def test_ats_title_and_commitment_internships_match_same_filter(self):
        def fake(url):
            if "greenhouse" in url:
                return {"jobs": [{"id": 1, "title": "Marketing Internship", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"}]}
            return [{"id": "2", "text": "Marketing", "hostedUrl": "https://jobs.lever.co/acme/2", "categories": {"commitment": "Intern / Full Time"}}]
        with patch.object(jobs, "_fetch_json", side_effect=fake):
            result = jobs.search_jobs("internship marketing", {"employment": "Magang"}, sources=[], boards=[
                {"type": "greenhouse", "slug": "acme"}, {"type": "lever", "slug": "acme"}])
        self.assertEqual(len(result["jobs"]), 2)

    def test_non_internship_type_spacing_and_case_are_canonical(self):
        rows = [self.freehire(url=f"https://example.com/{index}", enrichment={"employment_type": value})
                for index, value in enumerate([" Full Time ", "FULL_TIME", "full-time", "Part Time", "part_time", "Contract", "Manager"])]
        with patch.object(jobs, "_fetch_json", return_value={"data": rows}):
            result = jobs.search_jobs("frontend", sources=["freehire"])
        self.assertEqual([row["employment"] for row in result["jobs"]], [
            "Penuh waktu", "Penuh waktu", "Penuh waktu", "Paruh waktu", "Paruh waktu", "Kontrak", jobs.UNKNOWN])

    def test_generic_magang_queries_translate_aliases_without_forcing_type(self):
        cases = [("magang", "internship", "Frontend Intern"),
                 ("magang frontend", "internship frontend", "Frontend Intern"),
                 ("Praktikum finance", "internship finance", "Finance Internship")]
        for query, provider_query, title in cases:
            with self.subTest(query=query), patch.object(jobs, "_fetch_json", return_value={"data": [self.freehire(title=title)]}) as fetch:
                result = jobs.search_jobs(query, sources=["freehire"])
                params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
                self.assertEqual(params.get("q"), [provider_query])
                self.assertNotIn("employment_type", params)
                self.assertEqual(len(result["jobs"]), 1)

    def test_generic_program_manager_search_does_not_force_magang_type(self):
        row = self.freehire(title="Internship Program Manager", enrichment={"employment_type": "Full Time"})
        with patch.object(jobs, "_fetch_json", return_value={"data": [row]}) as fetch:
            result = jobs.search_jobs("magang program manager", sources=["freehire"])
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params.get("q"), ["internship program manager"])
        self.assertNotIn("employment_type", params)
        self.assertEqual(result["jobs"][0]["employment"], "Penuh waktu")

    def test_generic_internal_international_query_is_not_rewritten(self):
        row = self.freehire(title="International Internal Auditor")
        with patch.object(jobs, "_fetch_json", return_value={"data": [row]}) as fetch:
            result = jobs.search_jobs("International Internal", sources=["freehire"])
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params.get("q"), ["International Internal"])
        self.assertEqual(result["jobs"][0]["employment"], jobs.UNKNOWN)

    def test_seniority_uses_same_canonical_values_as_filter(self):
        cases = [(" senior ", "Senior"), ("Senior", "Senior"), ("JUNIOR", "Junior"),
                 ("middle", "Menengah"), ("Mid", "Menengah"), ("mid-level", "Menengah"),
                 ("mid_level", "Menengah"), ("Menengah", "Menengah"),
                 ("entry", "Pemula"), ("entry level", "Pemula"), ("ENTRY_LEVEL", "Pemula"), ("Pemula", "Pemula")]
        for raw, canonical in cases:
            row = self.freehire(enrichment={"seniority": raw})
            with self.subTest(raw=raw), patch.object(jobs, "_fetch_json", return_value={"data": [row]}):
                result = jobs.search_jobs("frontend", sources=["freehire"])
                filtered = jobs.search_jobs("frontend", {"level": canonical}, ["freehire"])
                alias = jobs.search_jobs("frontend", {"level": raw}, ["freehire"])
                self.assertEqual(result["jobs"][0]["level"], canonical)
                self.assertEqual(len(filtered["jobs"]), 1)
                self.assertEqual(len(alias["jobs"]), 1)

    def test_ambiguous_seniority_is_unknown_without_becoming_a_requested_tier(self):
        for raw in ("manager", "lead", "principal", "experienced", "intern", "Mid Senior", None, {"unexpected": "senior"}):
            row = self.freehire(enrichment={"seniority": raw})
            with self.subTest(raw=raw), patch.object(jobs, "_fetch_json", return_value={"data": [row]}):
                result = jobs.search_jobs("frontend", sources=["freehire"])
                known_tier = jobs.search_jobs("frontend", {"level": "Senior"}, ["freehire"])
                explicit_unknown = jobs.search_jobs("frontend", {"level": jobs.UNKNOWN}, ["freehire"])
                ambiguous_filter = jobs.search_jobs("frontend", {"level": "manager"}, ["freehire"])
                self.assertEqual(result["jobs"][0]["level"], jobs.UNKNOWN)
                self.assertEqual(known_tier["jobs"], [])
                self.assertEqual(len(explicit_unknown["jobs"]), 1)
                self.assertEqual(ambiguous_filter["jobs"], [])

    def test_ats_seniority_uses_same_canonical_filter_contract(self):
        row = {"id": "1", "text": "Frontend Engineer", "hostedUrl": "https://jobs.lever.co/acme/1", "categories": {"level": "Mid-level"}}
        with patch.object(jobs, "_fetch_json", return_value=[row]):
            result = jobs.search_jobs("frontend", {"level": "Menengah"}, sources=[], boards=[{"type": "lever", "slug": "acme"}])
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["level"], "Menengah")

    def test_plural_internship_aliases_match_without_losing_original_role(self):
        row = self.freehire(title="Frontend Intern", enrichment={"employment_type": "internship"})
        for alias in ("interns", "internships"):
            for filters in ({}, {"employment": "Magang"}):
                with self.subTest(alias=alias, filters=filters), patch.object(jobs, "_fetch_json", return_value={"data": [row]}) as fetch:
                    result = jobs.search_jobs(alias + " frontend", filters, ["freehire"])
                    self.assertEqual(len(result["jobs"]), 1)
                    params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
                    self.assertEqual(params.get("q"), ["frontend"] if filters else ["internship frontend"])

    def test_plural_internship_program_staff_is_not_a_magang_role(self):
        row = self.freehire(title="Internships Program Manager", enrichment={"employment_type": "full_time"})
        with patch.object(jobs, "_fetch_json", return_value={"data": [row]}):
            result = jobs.search_jobs("internships", sources=["freehire"])
            filtered = jobs.search_jobs("internships", {"employment": "Magang"}, ["freehire"])
        self.assertEqual(result["jobs"][0]["employment"], "Penuh waktu")
        self.assertEqual(filtered["jobs"], [])

    def test_c_cplusplus_csharp_queries_do_not_match_each_other(self):
        rows = [self.freehire(title=language + " Engineer", description="Build native applications.", skills=[language],
                             url=f"https://example.com/jobs/language-{index}") for index, language in enumerate(("C", "C++", "C#"))]
        for language in ("C", "C++", "C#"):
            with self.subTest(language=language), patch.object(jobs, "_fetch_json", return_value={"data": rows}):
                result = jobs.search_jobs(language + " engineer", sources=["freehire"])
                self.assertEqual([row["title"] for row in result["jobs"]], [language + " Engineer"])

    def test_language_markers_are_not_invented_inside_other_words(self):
        row = self.freehire(title="ABC# Engineer", description="Internal systems", skills=[])
        with patch.object(jobs, "_fetch_json", return_value={"data": [row]}):
            result = jobs.search_jobs("C# engineer", sources=["freehire"])
        self.assertEqual(result["jobs"], [])


class PortalSearchTests(unittest.TestCase):
    """Synthetic public listing shapes; these fixtures are not live-page evidence."""
    checked = '2026-10-25T10:00:00Z'
    glints_id = '650bedb6-c639-412d-a444-a85bf64b4265'

    def glints_page(self, *, changes=None, linked=True, root=None):
        row = {'__typename': 'Job', 'id': self.glints_id, 'title': 'Admin Operations', 'type': 'INTERNSHIP',
               'status': 'OPEN', 'createdAt': '2026-10-24T10:00:00Z', 'workArrangementOption': 'HYBRID',
               'company': {'__ref': 'Company:example'}, 'city': {'__ref': 'City:jakarta'},
               'country': {'__ref': 'Country:ID'}, 'skills': [{'__ref': 'Skill:excel'}]}
        row.update(changes or {})
        cache = {'Job:' + self.glints_id: row, 'Company:example': {'name': 'Perusahaan Contoh'},
                 'City:jakarta': {'name': 'Jakarta Selatan'}, 'Country:ID': {'code': 'ID', 'name': 'Indonesia'},
                 'Skill:excel': {'name': 'Excel'}, 'ROOT_QUERY': root if root is not None else {'searchJobs': {'jobs': [{'__ref': 'Job:' + self.glints_id}]}}}
        link = '<a href="/id/opportunities/jobs/admin-operations/' + self.glints_id + '?utm_referrer=explore&amp;traceInfo=public">Admin Operations</a>' if linked else ''
        return '<html>' + link + '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({'props': {'apolloCache': cache}}) + '</script></html>'

    def jobstreet_card(self, **changes):
        row = {'id': '12345678', 'title': 'Admin Intern', 'url': 'https://id.jobstreet.com/job/12345678?ref=search&type=standard',
               'company': 'Perusahaan Contoh', 'location': 'Jakarta Selatan, Jakarta',
               'description': 'Administrasi laporan Excel.', 'employment': 'Full time',
               'salary': 'Rp 2.000.000 per month', 'posted': '2026-10-24T10:00:00Z'}
        row.update(changes)
        return row

    def test_portal_urls_encode_one_segment_and_include_optional_internship_term(self):
        self.assertEqual(jobs.portal_search_url('glints', 'Front-end engineer'), 'https://glints.com/id/explore/front-end-engineer')
        self.assertEqual(jobs.portal_search_url('jobstreet', 'Admin HR', {'employment': 'Magang'}), 'https://id.jobstreet.com/magang-admin-hr-jobs')
        self.assertEqual(jobs.portal_search_url('glints', 'magang admin', {'employment': 'Magang'}), 'https://glints.com/id/explore/magang-admin')
        url = jobs.portal_search_url('jobstreet', 'admin/../../login?x=1#next')
        self.assertEqual(urlsplit(url).hostname, 'id.jobstreet.com')
        self.assertEqual(urlsplit(url).query, '')
        self.assertNotIn('/../', url)
        self.assertIn('%2F', url)

    def test_portal_url_rejects_unknown_provider_and_invalid_keyword(self):
        for provider, query in (('unknown', 'admin'), ('glints', ''), ('jobstreet', 'x' * 201), ('glints', None)):
            with self.subTest(provider=provider, query=query), self.assertRaises(ValueError):
                jobs.portal_search_url(provider, query)

    def test_glints_resolves_references_but_does_not_invent_company_verification_or_deadline(self):
        found = jobs.parse_glints_page(self.glints_page(), 'admin', {'employment': 'Magang'}, self.checked)
        self.assertEqual(len(found), 1)
        job = found[0]
        self.assertEqual((job['providerId'], job['sourceName'], job['company']), ('glints', 'Glints', 'Perusahaan Contoh'))
        self.assertEqual((job['country'], job['city'], job['employment'], job['mode']), ('Indonesia', 'Jakarta Selatan', 'Magang', 'Hybrid'))
        self.assertEqual(job['skills'], ['Excel'])
        self.assertEqual(job['status'], 'open')
        self.assertEqual(job['origin'], 'unknown')
        self.assertFalse(job['mock'])
        self.assertIsNone(job['deadline'])
        self.assertIsNone(job['evidence']['http_status'])
        self.assertEqual(job['evidence']['method'], 'public_page')
        self.assertEqual(job['checkedAt'], self.checked)
        self.assertEqual(job['sourceUrl'], 'https://glints.com/id/opportunities/jobs/admin-operations/' + self.glints_id)
        self.assertNotIn('traceInfo', json.dumps(job))
        self.assertIsInstance(job['id'], int)

    def test_glints_only_includes_linked_jobs_and_requires_real_search_contract(self):
        for page in ('<html>Sign in</html>', '<script id="__NEXT_DATA__">not json</script>',
                     self.glints_page(linked=False), '<script id="__NEXT_DATA__">{"props":{"apolloCache":{}}}</script>'):
            with self.subTest(page=page[:50]), self.assertRaises(jobs.FetchError):
                jobs.parse_glints_page(page, 'admin', {}, self.checked)
        empty = '<script id="__NEXT_DATA__">' + json.dumps({'props': {'apolloCache': {'ROOT_QUERY': {'searchJobs': {'jobs': []}}}}}) + '</script>'
        self.assertEqual(jobs.parse_glints_page(empty, 'admin', {}, self.checked), [])

    def test_glints_applies_original_keyword_country_and_employment_filters(self):
        page = self.glints_page(changes={'type': 'FULL_TIME', 'title': 'International Admin'})
        self.assertEqual(jobs.parse_glints_page(page, 'admin', {'employment': 'Magang'}, self.checked), [])
        self.assertEqual(jobs.parse_glints_page(self.glints_page(), 'accountant', {}, self.checked), [])
        self.assertEqual(jobs.parse_glints_page(self.glints_page(), 'admin', {'countries': ['Jerman']}, self.checked), [])
        self.assertEqual(len(jobs.parse_glints_page(self.glints_page(), 'magang admin', {'countries': ['Indonesia']}, self.checked)), 1)

    def test_glints_closed_source_status_is_hidden_by_default_and_unknown_stays_unknown(self):
        closed = self.glints_page(changes={'status': 'closed', 'createdAt': 'not a date'})
        self.assertEqual(jobs.parse_glints_page(closed, 'admin', {}, self.checked), [])
        archived = jobs.parse_glints_page(closed, 'admin', {'status': 'all'}, self.checked)[0]
        self.assertEqual((archived['status'], archived['evidence']['status']), ('closed', 'closed'))
        self.assertIsNone(archived['publishedAt'])
        unknown = jobs.parse_glints_page(self.glints_page(changes={'status': 'PENDING'}), 'admin', {}, self.checked)[0]
        self.assertEqual(unknown['status'], 'unknown')

    def test_portal_explicit_status_is_normalized_and_open_filter_excludes_unknown(self):
        for provider in ('glints', 'jobstreet'):
            for source_status, expected in (('OPEN', 'open'), ('CLOSED', 'closed'),
                                            ('EXPIRED', 'expired'), ('PENDING', 'unknown')):
                with self.subTest(provider=provider, status=source_status):
                    if provider == 'glints':
                        payload = self.glints_page(changes={'status': source_status})
                        parse = jobs.parse_glints_page
                    else:
                        payload = [self.jobstreet_card(status=source_status)]
                        parse = jobs.parse_jobstreet_cards
                    archived = parse(payload, 'admin', {'status': 'all'}, self.checked)[0]
                    self.assertEqual((archived['status'], archived['evidence']['status']), (expected, expected))
                    opened = parse(payload, 'admin', {'status': 'open'}, self.checked)
                    self.assertEqual(len(opened), 1 if expected == 'open' else 0)

    def test_glints_source_deadline_expires_open_jobs_without_reopening_closed_jobs(self):
        past, future = '2026-10-24T10:00:00Z', '2026-10-26T10:00:00Z'
        for field in ('validThrough', 'expiryDate'):
            for source_status, deadline, expected in (('OPEN', past, 'expired'), ('OPEN', future, 'open'),
                                                       ('CLOSED', future, 'closed'), ('PENDING', future, 'unknown')):
                with self.subTest(field=field, status=source_status, deadline=deadline):
                    payload = self.glints_page(changes={'status': source_status, field: deadline})
                    archived = jobs.parse_glints_page(payload, 'admin', {'status': 'all'}, self.checked)[0]
                    self.assertEqual((archived['status'], archived['evidence']['status']), (expected, expected))
                    self.assertEqual(archived['deadline'], deadline.replace('Z', '+00:00'))
                    opened = jobs.parse_glints_page(payload, 'admin', {'status': 'open'}, self.checked)
                    self.assertEqual(len(opened), 1 if expected == 'open' else 0)

    def test_jobstreet_detail_deadline_expires_open_jobs_without_reopening_closed_jobs(self):
        past, future = '2026-10-24T10:00:00Z', '2026-10-26T10:00:00Z'
        for source_status, deadline, expected in (('OPEN', past, 'expired'), ('OPEN', future, 'open'),
                                                   ('CLOSED', future, 'closed'), ('PENDING', future, 'unknown')):
            with self.subTest(status=source_status, deadline=deadline):
                payload = [self.jobstreet_card(status=source_status, deadline=deadline)]
                archived = jobs.parse_jobstreet_cards(payload, 'admin', {'status': 'all'}, self.checked)[0]
                self.assertEqual((archived['status'], archived['evidence']['status']), (expected, expected))
                self.assertEqual(archived['deadline'], deadline.replace('Z', '+00:00'))
                opened = jobs.parse_jobstreet_cards(payload, 'admin', {'status': 'open'}, self.checked)
                self.assertEqual(len(opened), 1 if expected == 'open' else 0)

    def test_detail_requires_matching_job_and_positive_application_evidence(self):
        for changes, expected in [({}, 'open'), ({'can_apply': False}, 'unknown'),
                                  ({'title_matches': False}, 'unknown'), ({'http_status': 403}, 'unknown'),
                                  ({'http_status': 404}, 'closed'), ({'closed_status': 'closed'}, 'closed'),
                                  ({'url': 'https://evil.example/job/12345678'}, 'unknown')]:
            with self.subTest(changes=changes):
                job = jobs.parse_jobstreet_cards([self.jobstreet_card()], 'admin', {}, self.checked)[0]
                detail = {'url': job['sourceUrl'], 'http_status': 200, 'html': '',
                          'can_apply': True, 'title_matches': True, **changes}
                jobs.apply_portal_detail(job, detail, self.checked)
                self.assertEqual(job['status'], expected)
                self.assertEqual(job['evidence']['status'], expected)

    def test_detail_expired_metadata_overrides_visible_application_button(self):
        job = jobs.parse_jobstreet_cards([self.jobstreet_card()], 'admin', {}, self.checked)[0]
        metadata = {'@type': 'JobPosting', 'url': job['sourceUrl'], 'validThrough': '2001-01-01'}
        detail = {'url': job['sourceUrl'], 'http_status': 200,
                  'html': '<script type="application/ld+json">' + json.dumps(metadata) + '</script>',
                  'can_apply': True, 'title_matches': True}
        jobs.apply_portal_detail(job, detail, self.checked)
        self.assertEqual(job['status'], 'expired')
        self.assertEqual(job['deadline'], '2001-01-01T23:59:59.999999+00:00')

    def test_glints_detail_status_is_taken_only_from_matching_public_job_record(self):
        job = jobs.parse_glints_page(self.glints_page(), 'admin', {}, self.checked)[0]
        detail = {'url': job['sourceUrl'], 'http_status': 200,
                  'html': self.glints_page(changes={'status': 'CLOSED'}),
                  'can_apply': True, 'title_matches': True}
        jobs.apply_portal_detail(job, detail, self.checked)
        self.assertEqual(job['status'], 'closed')

    def test_related_job_metadata_cannot_expire_the_requested_posting(self):
        job = jobs.parse_jobstreet_cards([self.jobstreet_card()], 'admin', {}, self.checked)[0]
        metadata = {'@type': 'JobPosting', 'url': 'https://id.jobstreet.com/job/99999999',
                    'validThrough': '2001-01-01'}
        detail = {'url': job['sourceUrl'], 'http_status': 200,
                  'html': '<script type="application/ld+json">' + json.dumps(metadata) + '</script>',
                  'can_apply': True, 'title_matches': True}
        jobs.apply_portal_detail(job, detail, self.checked)
        self.assertEqual(job['status'], 'open')
        self.assertIsNone(job['deadline'])

    def test_glints_skips_foreign_or_unsafe_links_without_reading_detail_pages(self):
        page = self.glints_page()
        page += '<a href="https://evil.example/id/opportunities/jobs/admin/' + self.glints_id + '">Admin</a>'
        page += '<a href="http://127.0.0.1/id/opportunities/jobs/admin/' + self.glints_id + '">Admin</a>'
        page += '<a href="https://user:password@glints.com/id/opportunities/jobs/admin/' + self.glints_id + '">Admin</a>'
        with patch.object(jobs, '_fetch') as fetch:
            found = jobs.parse_glints_page(page, 'admin', {}, self.checked)
        self.assertEqual(len(found), 1)
        fetch.assert_not_called()

    def test_portal_parsers_reject_malformed_hosts_and_paths(self):
        for url in ('https://[invalid]/job/12345678', 'https://id.jobstreet.com/%2e%2e/job/12345678',
                    'https://id.jobstreet.com/job/12345678?session_id=private'):
            with self.subTest(url=url):
                self.assertEqual(jobs.parse_jobstreet_cards([self.jobstreet_card(url=url)], 'admin', {}, self.checked), [])
        page = self.glints_page()
        unsafe = page.replace('/id/opportunities/jobs/admin-operations/', '/id/opportunities/jobs/%2e%2e/')
        with self.assertRaises(jobs.FetchError):
            jobs.parse_glints_page(unsafe, 'admin', {}, self.checked)

    def test_glints_html_limit_counts_utf8_bytes(self):
        page = self.glints_page()
        with patch.object(jobs, 'MAX_RESPONSE_BYTES', len(page.encode('utf-8')) + 20), self.assertRaises(jobs.FetchError):
            jobs.parse_glints_page(page + 'é' * 11, 'admin', {}, self.checked)

    def test_jobstreet_cards_are_bounded_filtered_and_keep_listing_provenance(self):
        cards = [self.jobstreet_card(id=str(100 + index), url=f'/job/{100 + index}') for index in range(35)]
        with patch.object(jobs, '_fetch') as fetch:
            found = jobs.parse_jobstreet_cards(cards, 'admin', {'employment': 'Magang'}, self.checked)
        self.assertEqual(len(found), 35)
        job = found[0]
        self.assertEqual((job['providerId'], job['sourceName'], job['country']), ('jobstreet', 'Jobstreet', 'Indonesia'))
        self.assertEqual((job['city'], job['region']), ('Jakarta Selatan', 'Jakarta'))
        self.assertEqual(job['salary'], 'Rp 2.000.000 per month')
        self.assertEqual(job['evidence']['method'], 'local_browser')
        self.assertEqual((job['status'], job['origin']), ('unknown', 'unknown'))
        self.assertIsNone(job['deadline'])
        self.assertIsNone(job['evidence']['http_status'])
        self.assertEqual(job['checkedAt'], self.checked)
        fetch.assert_not_called()

    def test_jobstreet_skips_unsafe_urls_and_deduplicates_tracking_variants(self):
        cards = [self.jobstreet_card(), self.jobstreet_card(url='/job/12345678?utm_source=other'),
                 self.jobstreet_card(url='https://evil.example/job/12345678'),
                 self.jobstreet_card(url='http://127.0.0.1/job/12345678'),
                 self.jobstreet_card(url='https://user:pass@id.jobstreet.com/job/12345678'),
                 self.jobstreet_card(url='https://id.jobstreet.com/job/12345678?token=private'),
                 self.jobstreet_card(url='https://id.jobstreet.com/profile')]
        found = jobs.parse_jobstreet_cards(cards, 'admin', {}, self.checked)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['sourceUrl'], 'https://id.jobstreet.com/job/12345678')

    def test_jobstreet_requires_card_contract_and_never_infers_internship_from_description(self):
        for cards in (None, {}, 'login', [{}], [{'id': 'x', 'title': 'Login'}]):
            with self.subTest(cards=cards), self.assertRaises(jobs.FetchError):
                jobs.parse_jobstreet_cards(cards, 'admin', {}, self.checked)
        self.assertEqual(jobs.parse_jobstreet_cards([], 'admin', {}, self.checked), [])
        row = self.jobstreet_card(title='Internal Admin', description='Support our internship program.', posted='2 days ago')
        self.assertEqual(jobs.parse_jobstreet_cards([row], 'admin', {'employment': 'Magang'}, self.checked), [])
        found = jobs.parse_jobstreet_cards([row], 'admin', {}, self.checked)[0]
        self.assertIsNone(found['publishedAt'])
        self.assertEqual(found['employment'], 'Penuh waktu')
        self.assertEqual(jobs.parse_jobstreet_cards([self.jobstreet_card(posted=1690000000)], 'admin', {}, self.checked), [])

    def test_portal_callback_receives_original_query_and_keeps_sources_independent(self):
        calls = []
        def portal(definition, query, filters, checked):
            calls.append((definition['id'], query, filters, checked))
            if definition['id'] == 'jobstreet':
                raise jobs.FetchError('blocked', 'Sumber meminta tindakan Anda di browser.')
            return jobs.parse_glints_page(self.glints_page(), query, filters, checked)
        with patch.object(jobs, '_fetch') as fetch, patch.object(jobs, '_fetch_json') as fetch_json:
            result = jobs.search_jobs('admin', {'employment': 'Magang'}, sources=['jobstreet', 'glints'], portal_search=portal)
        self.assertTrue(result['ok'])
        self.assertEqual([source['status'] for source in result['sources']], ['error', 'ok'])
        self.assertEqual(result['sources'][0]['code'], 'blocked')
        self.assertEqual([job['providerId'] for job in result['jobs']], ['glints'])
        self.assertCountEqual([call[0] for call in calls], ['glints', 'jobstreet'])
        self.assertTrue(all(call[1] == 'admin' and call[3] == result['searched_at'] for call in calls))
        fetch.assert_not_called()
        fetch_json.assert_not_called()

    def test_portal_callback_is_not_called_for_country_or_source_skips(self):
        for filters in ({'countries': ['Jerman']}, {'sourceType': 'company'}):
            with self.subTest(filters=filters):
                calls = []
                result = jobs.search_jobs('admin', filters, sources=['glints', 'jobstreet'],
                                          portal_search=lambda *args: calls.append(args) or [])
                self.assertEqual(calls, [])
                self.assertTrue(all(source['status'] == 'skipped' for source in result['sources']))

    def test_portal_callback_keeps_page_results_and_reports_changed_output_shape(self):
        row = jobs._base_job({'name': 'Jobstreet', 'api': 'https://id.jobstreet.com/admin-jobs'}, self.checked,
                             title='Admin Intern', company='Contoh', url='https://id.jobstreet.com/job/1')
        rows = [{**row, 'sourceUrl': f'https://id.jobstreet.com/job/{index}'} for index in range(35)]
        result = jobs.search_jobs('admin', sources=['jobstreet'], portal_search=lambda *args: rows)
        self.assertEqual(len(result['jobs']), 35)
        bad = jobs.search_jobs('admin', sources=['jobstreet'], portal_search=lambda *args: {'jobs': 'invalid'})
        self.assertEqual(bad['sources'][0]['status'], 'error')


class PaginationTests(unittest.TestCase):
    def freehire(self, index=0, **changes):
        return SearchTests().freehire(url=f'https://example.com/jobs/{index}', **changes)

    def test_freehire_metadata_continues_past_first_page_and_stops_at_end(self):
        calls = []
        def fetch(url):
            calls.append(url)
            offset = int(parse_qs(urlsplit(url).query)['offset'][0])
            rows = [self.freehire(index) for index in range(offset, min(offset + 20, 25))]
            return {'data': rows, 'meta': {'total': 25, 'offset': offset, 'limit': 20}}
        with patch.object(jobs, '_fetch_json', side_effect=fetch):
            first = jobs.search_jobs('frontend', sources=['freehire'])
            cursor = first['sources'][0]['next_cursor']
            second = jobs.search_jobs('frontend', sources=['freehire'], cursors={'freehire': cursor})
        self.assertEqual([len(result['jobs']) for result in (first, second)], [20, 5])
        self.assertEqual(parse_qs(urlsplit(cursor).query)['offset'], ['20'])
        self.assertEqual(second['sources'][0]['fetched_count'], 5)
        self.assertIsNone(second['sources'][0]['next_cursor'])
        self.assertTrue(set(j['id'] for j in first['jobs']).isdisjoint(j['id'] for j in second['jobs']))

    def test_continuation_requests_only_pending_sources(self):
        cursor = 'https://www.arbeitnow.com/api/job-board-api?page=2'
        with patch.object(jobs, '_fetch_json', return_value={'data': [], 'links': {'next': None}}) as fetch:
            result = jobs.search_jobs('admin', sources=['freehire', 'arbeitnow'], cursors={'arbeitnow': cursor})
        self.assertEqual([row['id'] for row in result['sources']], ['arbeitnow'])
        fetch.assert_called_once_with(cursor)

    def test_arbeitnow_next_survives_empty_keyword_page(self):
        cursor = 'https://www.arbeitnow.com/api/job-board-api?page=2'
        payload = {'data': [{'title': 'Nurse', 'company_name': 'Clinic', 'url': 'https://example.com/nurse'}],
                   'links': {'next': cursor}}
        with patch.object(jobs, '_fetch_json', return_value=payload):
            result = jobs.search_jobs('frontend', sources=['arbeitnow'])
        self.assertEqual(result['jobs'], [])
        self.assertEqual((result['sources'][0]['fetched_count'], result['sources'][0]['next_cursor']), (1, cursor))

    def test_cursor_rejects_foreign_hosts_paths_query_changes_and_repeated_pages(self):
        bad = ['https://127.0.0.1/api/job-board-api?page=2', 'https://evil.test/api/job-board-api?page=2',
               'http://www.arbeitnow.com/api/job-board-api?page=2', 'https://www.arbeitnow.com/admin?page=2',
               'https://www.arbeitnow.com/api/job-board-api?page=2&token=private',
               'https://www.arbeitnow.com/api/job-board-api?page=1',
               'https://www.arbeitnow.com/api/job-board-api?page=-1',
               'https://www.arbeitnow.com/api/job-board-api?page=2&redirect=https://evil.test']
        for cursor in bad:
            with self.subTest(cursor=cursor), patch.object(jobs, '_fetch_json') as fetch:
                result = jobs.search_jobs('admin', sources=['arbeitnow'], cursors={'arbeitnow': cursor})
            self.assertFalse(result['ok'])
            fetch.assert_not_called()
        with patch.object(jobs, '_fetch_json') as fetch:
            invalid = jobs.search_jobs('admin', sources=['arbeitnow'], cursors={'freehire': 'https://freehire.me/x'})
        self.assertFalse(invalid['ok'])
        fetch.assert_not_called()

    def test_untrusted_next_link_cannot_change_source_or_loop(self):
        for next_url in ('https://evil.test/api/job-board-api?page=2', 'https://www.arbeitnow.com/api/job-board-api?page=1'):
            with self.subTest(next_url=next_url), patch.object(jobs, '_fetch_json', return_value={'data': [], 'links': {'next': next_url}}):
                result = jobs.search_jobs('admin', sources=['arbeitnow'])
            self.assertEqual(result['sources'][0]['status'], 'error')
            self.assertIsNone(result['sources'][0]['next_cursor'])

    def test_kalibrr_follows_only_explicit_next_link(self):
        fixture = SearchTests()
        page = fixture.kalibrr_page([fixture.kalibrr()])
        page['body'] += b'<a rel="next" href="/id-ID/home/te/administrasi?page=2">Berikutnya</a>'
        cursor = 'https://www.kalibrr.id/id-ID/home/te/administrasi?page=2'
        with patch.object(jobs, '_fetch', return_value=page):
            result = jobs.search_jobs('administrasi', sources=['kalibrr'])
        self.assertEqual(result['sources'][0]['next_cursor'], cursor)
        self.assertEqual(result['sources'][0]['fetched_count'], 1)
        with patch.object(jobs, '_fetch', return_value=fixture.kalibrr_page([])) as fetch:
            empty = jobs.search_jobs('administrasi', sources=['kalibrr'], cursors={'kalibrr': cursor})
        self.assertEqual(fetch.call_args.args[0], cursor)
        self.assertIsNone(empty['sources'][0]['next_cursor'])

    def test_freehire_provider_depth_limit_is_reported_instead_of_fake_completion(self):
        _, _, definitions = jobs._search_inputs('frontend', {}, ['freehire'], [])
        cursor = definitions[0]['api'].replace('offset=0', 'offset=9980')
        payload = {'data': [self.freehire(i) for i in range(20)], 'meta': {'total': 12000, 'offset': 9980, 'limit': 20}}
        with patch.object(jobs, '_fetch_json', return_value=payload):
            result = jobs.search_jobs('frontend', sources=['freehire'], cursors={'freehire': cursor})
        self.assertEqual(len(result['jobs']), 20)
        self.assertIsNone(result['sources'][0]['next_cursor'])
        self.assertEqual(result['sources'][0]['pagination_limited'], True)
        self.assertIn('sumber', result['sources'][0]['message'])

    def test_portal_callback_page_contract_preserves_opaque_cursor(self):
        rows = jobs.parse_jobstreet_cards([PortalSearchTests().jobstreet_card()], 'admin', {}, PortalSearchTests.checked)
        calls = []
        def portal(definition, query, filters, checked):
            calls.append(definition)
            return {'jobs': rows, 'next_cursor': 'scroll:next', 'fetched_count': 30}
        result = jobs.search_jobs('admin', sources=['jobstreet'], cursors={'jobstreet': 'scroll:current'}, portal_search=portal)
        self.assertEqual(calls[0]['cursor'], 'scroll:current')
        self.assertEqual(result['sources'][0]['next_cursor'], 'scroll:next')
        self.assertEqual(result['sources'][0]['fetched_count'], 30)

    def test_google_is_discovery_callback_and_not_indonesia_portal_filtered(self):
        calls = []
        result = jobs.search_jobs('nurse', {'countries': ['Jerman'], 'sourceType': 'company'}, sources=['google'],
                                  portal_search=lambda *args: calls.append(args[0]) or {'jobs': [], 'fetched_count': 0, 'next_cursor': None})
        self.assertTrue(result['ok'])
        self.assertEqual(calls[0]['id'], 'google')
        self.assertEqual(calls[0]['sourceType'], 'web')
        self.assertEqual(urlsplit(calls[0]['search_url']).hostname, 'www.google.com')
        self.assertEqual(result['sources'][0]['status'], 'ok')

    def test_google_careers_targets_company_career_paths_and_ats_boards(self):
        calls = []
        result = jobs.search_jobs('finance', {'countries': ['Indonesia']}, sources=['google-careers'],
                                  portal_search=lambda *args: calls.append(args[0]) or [])
        self.assertTrue(result['ok'])
        definition = calls[0]
        self.assertEqual(definition['sourceType'], 'web')
        search = parse_qs(urlsplit(definition['search_url']).query)['q'][0]
        self.assertTrue(search.startswith('finance '))
        self.assertIn('inurl:careers', search)
        self.assertIn('inurl:karir', search)
        self.assertIn('site:jobs.lever.co', search)
        self.assertIn('site:boards.greenhouse.io', search)

    def test_admin_aliases_are_word_scoped_and_do_not_change_language_markers(self):
        for query in ('admin', 'administrasi', 'administration'):
            with self.subTest(query=query), patch.object(jobs, '_fetch_json', return_value={'data': [self.freehire(title='Admin Operations')]}):
                result = jobs.search_jobs(query, sources=['freehire'])
            self.assertEqual(len(result['jobs']), 1)
        self.assertEqual(jobs._norm('administrator C C++ C#'), 'administrator c cplusplus csharp')

    def test_native_portal_relevance_keeps_synonyms_but_not_other_queries_or_filters(self):
        fixture = PortalSearchTests()
        card = fixture.jobstreet_card(title='Office Support', description='Manage scheduling.', employment='Full time')
        found = jobs.parse_jobstreet_cards([card], 'administrasi', {}, fixture.checked, native_search=True)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['searchQuery'], 'administrasi')
        self.assertTrue(jobs._matches(found[0], 'admin', {}))
        self.assertFalse(jobs._matches(found[0], 'nurse', {}))
        self.assertFalse(jobs._matches(found[0], 'admin', {'employment': 'Magang'}))
        self.assertEqual(jobs.parse_jobstreet_cards([card], 'administrasi', {}, fixture.checked), [])
        csharp = fixture.jobstreet_card(title='C# Engineer', description='Build systems.')
        self.assertEqual(jobs.parse_jobstreet_cards([csharp], 'C++ engineer', {}, fixture.checked, native_search=True), [])

    def test_glints_native_relevance_and_description_remain_query_scoped(self):
        fixture = PortalSearchTests()
        page = fixture.glints_page(changes={'title': 'Office Support', 'description': 'Administrative reports'})
        found = jobs.parse_glints_page(page, 'administrasi', {}, fixture.checked, native_search=True)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['description'], 'Administrative reports')
        self.assertEqual(found[0]['searchQuery'], 'administrasi')
        self.assertFalse(jobs._matches(found[0], 'nurse', {}))

    def test_large_company_board_response_is_not_silently_truncated(self):
        rows = [{'id': i, 'title': 'Admin', 'absolute_url': f'https://example.com/jobs/{i}'} for i in range(501)]
        with patch.object(jobs, '_fetch_json', return_value={'jobs': rows}):
            result = jobs.search_jobs('admin', sources=[], boards=[{'type': 'greenhouse', 'slug': 'example'}])
        self.assertEqual(len(result['jobs']), 501)
        self.assertEqual(result['sources'][0]['fetched_count'], 501)
        self.assertIsNone(result['sources'][0]['next_cursor'])

    def test_lever_continues_after_full_provider_page(self):
        def fetch(url):
            offset = int(parse_qs(urlsplit(url).query).get('skip', ['0'])[0])
            return [{'id': str(i), 'text': 'Admin', 'hostedUrl': f'https://jobs.lever.co/example/{i}'}
                    for i in range(offset, min(offset + 500, 503))]
        with patch.object(jobs, '_fetch_json', side_effect=fetch):
            first = jobs.search_jobs('admin', sources=[], boards=[{'type': 'lever', 'slug': 'example'}])
            second = jobs.search_jobs('admin', sources=[], boards=[{'type': 'lever', 'slug': 'example'}],
                                      cursors={'lever:example': first['sources'][0]['next_cursor']})
        self.assertEqual([len(first['jobs']), len(second['jobs'])], [500, 3])
        self.assertIsNone(second['sources'][0]['next_cursor'])

    def test_oversized_page_is_explicit_error_without_silent_data_loss(self):
        with patch.object(jobs, 'MAX_PAGE_ROWS', 2), patch.object(jobs, '_fetch_json', return_value={'data': [self.freehire(i) for i in range(3)]}):
            result = jobs.search_jobs('frontend', sources=['freehire'])
        self.assertEqual(result['sources'][0]['status'], 'error')
        self.assertEqual(result['sources'][0]['code'], 'too_large')

    def test_freehire_cursor_cannot_change_query_or_filter(self):
        _, _, definitions = jobs._search_inputs('frontend', {'countries': ['Indonesia']}, ['freehire'], [])
        original = definitions[0]['api']
        for cursor in (original.replace('offset=0', 'offset=20').replace('q=frontend', 'q=nurse'),
                       original.replace('offset=0', 'offset=20').replace('countries=ID', 'countries=DE'),
                       original.replace('offset=0', 'offset=20').replace('limit=20', 'limit=100')):
            with self.subTest(cursor=cursor), patch.object(jobs, '_fetch_json') as fetch:
                result = jobs.search_jobs('frontend', {'countries': ['Indonesia']}, ['freehire'], cursors={'freehire': cursor})
                self.assertFalse(result['ok'])
                fetch.assert_not_called()

    def test_invalid_freehire_pagination_metadata_does_not_claim_completion(self):
        for meta in ({'total': 40}, {'total': 40, 'offset': 20, 'limit': 20},
                     {'total': 40, 'offset': 0, 'limit': 0}, {'total': True, 'offset': 0, 'limit': 20}):
            with self.subTest(meta=meta), patch.object(jobs, '_fetch_json', return_value={'data': [self.freehire()], 'meta': meta}):
                result = jobs.search_jobs('frontend', sources=['freehire'])
                self.assertEqual(result['sources'][0]['status'], 'error')

    def test_four_browser_sources_execute_sequentially_and_keep_every_result(self):
        guard = threading.Lock()
        active = maximum = 0
        def portal(definition, query, filters, checked):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.015)
            with guard:
                active -= 1
            source = {'name': definition['name'], 'api': definition['search_url']}
            return [jobs._base_job(source, checked, title='Admin', company='Example',
                                   url='https://example.com/jobs/' + definition['id'])]
        result = jobs.search_jobs('admin', sources=['glints', 'jobstreet', 'google', 'google-careers'], portal_search=portal)
        self.assertEqual(maximum, 1)
        self.assertEqual(len(result['jobs']), 4)
        self.assertEqual([row['status'] for row in result['sources']], ['ok'] * 4)

    def test_public_sources_still_execute_in_parallel(self):
        rendezvous = threading.Barrier(2)
        def public(*args):
            rendezvous.wait(timeout=2)
            return {'jobs': [], 'fetched_count': 0, 'next_cursor': None}
        with patch.object(jobs, '_source_jobs', side_effect=public):
            result = jobs.search_jobs('admin', sources=['freehire', 'arbeitnow'])
        self.assertEqual([row['status'] for row in result['sources']], ['ok', 'ok'])

    def test_partial_portal_warning_preserves_rows_cursor_and_action_code(self):
        job = jobs._base_job({'name': 'Glints', 'api': 'https://glints.com/id/explore/admin'},
                             '2026-10-25T00:00:00Z', title='Admin', company='Example', url='https://example.com/jobs/admin')
        page = {'jobs': [job], 'fetched_count': 30, 'next_cursor': 'scroll:next',
                'warning': 'Login diperlukan untuk hasil berikutnya.', 'code': 'action_required', 'pagination_limited': True}
        result = jobs.search_jobs('admin', sources=['glints'], portal_search=lambda *args: page)
        self.assertEqual(result['jobs'], [job])
        status = result['sources'][0]
        self.assertEqual((status['status'], status['count'], status['next_cursor']), ('ok', 1, 'scroll:next'))
        self.assertEqual(status['warning'], page['warning'])
        self.assertEqual(status['code'], 'action_required')
        self.assertTrue(status['pagination_limited'])
        self.assertIn(page['warning'], status['message'])
        retry = jobs.search_jobs('admin', sources=['glints'], cursors={'glints': 'scroll:next'}, portal_search=lambda *args: page)
        self.assertEqual(retry['sources'][0]['next_cursor'], 'scroll:next')
        self.assertEqual(retry['sources'][0]['status'], 'ok')
        ordinary = {key: value for key, value in page.items() if key not in ('warning', 'code', 'pagination_limited')}
        loop = jobs.search_jobs('admin', sources=['glints'], cursors={'glints': 'scroll:next'}, portal_search=lambda *args: ordinary)
        self.assertEqual(loop['sources'][0]['status'], 'error')


class URLSafetyTests(unittest.TestCase):
    def test_private_special_and_credentials_are_rejected_before_connection(self):
        urls = ["file:///etc/passwd", "http://127.0.0.1/x", "http://10.0.0.1/", "http://169.254.169.254/",
                "http://[::1]/", "http://[fec0::1]/", "http://[::ffff:127.0.0.1]/", "https://a:b@example.com/",
                "https://example.com/?api_key=hidden", "https://example.com/?apikey=hidden", "https://example.com/?accessToken=hidden",
                "https://example.com:9000/", "https://example.com/\r\nx:y"]
        with patch.object(jobs, "_request_once") as request:
            for url in urls:
                with self.subTest(url=url):
                    result = jobs.verify_source(url)
                    self.assertFalse(result["ok"])
        request.assert_not_called()

    def test_dns_answer_containing_private_ip_is_rejected(self):
        answers = [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(jobs.socket, "getaddrinfo", return_value=answers), patch.object(jobs, "_request_once") as request:
            result = jobs.verify_source("https://example.com/job")
        self.assertFalse(result["ok"])
        request.assert_not_called()

    def test_redirect_to_private_target_is_never_connected(self):
        def fake(url, addresses, **kwargs):
            if url.endswith("robots.txt"):
                return response("", 404, "text/plain", url)
            return {"url": url, "status": 302, "headers": {"location": "http://127.0.0.1/admin"}, "body": b""}
        with patch.object(jobs, "_resolve_public", return_value=["93.184.216.34"]), patch.object(jobs, "_request_once", side_effect=fake) as request:
            result = jobs.verify_source("https://example.com/job")
        self.assertFalse(result["ok"])
        self.assertEqual(request.call_count, 2)

    def test_connection_uses_resolved_ip_and_tls_hostname(self):
        with patch.object(jobs.socket, "create_connection") as connect, patch.object(jobs.ssl, "create_default_context") as context, patch.object(jobs.http.client, "HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            connection.return_value.getresponse.return_value.getheaders.return_value = [("Content-Type", "text/html")]
            connection.return_value.getresponse.return_value.read1.return_value = b""
            jobs._request_once("https://example.com/job", ["93.184.216.34"], accept="text/html", max_bytes=100)
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))
        self.assertEqual(context.return_value.wrap_socket.call_args.kwargs["server_hostname"], "example.com")

    def test_international_url_becomes_ascii_uri_without_changing_query(self):
        normalized = jobs._validate_url("https://example.com/karier/insinyur-é?q=café&job=42")
        self.assertIn("insinyur-%C3%A9", normalized)
        self.assertIn("q=caf%C3%A9&job=42", normalized)

    def test_response_size_limit_prevents_body_read(self):
        with patch.object(jobs.socket, "create_connection"), patch.object(jobs.ssl, "create_default_context"), patch.object(jobs.http.client, "HTTPSConnection") as connection:
            response_mock = connection.return_value.getresponse.return_value
            response_mock.status = 200
            response_mock.getheaders.return_value = [("Content-Length", "200")]
            with self.assertRaises(jobs.FetchError):
                jobs._request_once("https://example.com/job", ["93.184.216.34"], accept="text/html", max_bytes=100)
        response_mock.read1.assert_not_called()

    def test_known_content_length_can_close_socket_after_last_body_chunk(self):
        with patch.object(jobs.socket, "create_connection"), patch.object(jobs.ssl, "create_default_context") as context, patch.object(jobs.http.client, "HTTPSConnection") as connection:
            sock = context.return_value.wrap_socket.return_value
            sock.settimeout.side_effect = [None, OSError("socket already closed after Content-Length bytes")]
            response_mock = connection.return_value.getresponse.return_value
            response_mock.status = 200
            response_mock.getheaders.return_value = [("Content-Length", "3")]
            response_mock.read1.side_effect = [b"abc", b""]
            response_mock.isclosed.return_value = True
            result = jobs._request_once("https://example.com/job", ["93.184.216.34"], accept="text/html", max_bytes=100)
        self.assertEqual(result["body"], b"abc")


class VerificationTests(unittest.TestCase):
    def checked(self, text, code=200):
        with patch.object(jobs, "_fetch", return_value=response(text, code, "text/html")):
            return jobs.verify_source("https://example.com/jobs")

    def test_http_200_is_not_proof_of_open_job_or_authenticity(self):
        result = self.checked("<html>Careers</html>")
        self.assertTrue(result["ok"])
        self.assertEqual(result["evidence"]["status"], "unknown")
        self.assertEqual(result["evidence"]["origin"], "unknown")
        self.assertFalse(result["evidence"]["posting_present"])

    def test_jsonld_graph_expired_job_is_reported_with_factual_metadata(self):
        metadata = {"@graph": [{"@type": "JobPosting", "title": "Nurse", "validThrough": "2001-01-01",
                    "hiringOrganization": {"name": "Clinic"}, "url": "https://example.com/jobs/1"}]}
        result = self.checked('<script type="application/ld+json">' + json.dumps(metadata) + '</script>')
        self.assertEqual(result["evidence"]["status"], "expired")
        self.assertEqual(result["evidence"]["company"], "Clinic")
        self.assertTrue(result["evidence"]["posting_present"])

    def test_future_expiry_and_unrelated_graph_node_do_not_claim_safe(self):
        metadata = [{"@type": "Organization", "name": "Wrong"}, {"@type": ["Thing", "JobPosting"], "title": "Engineer", "validThrough": "2099-01-01"}]
        result = self.checked('<script type="application/ld+json">' + json.dumps(metadata) + '</script>')
        self.assertEqual(result["evidence"]["status"], "unknown")
        self.assertEqual(result["evidence"]["title"], "Engineer")

    def test_related_expired_posting_does_not_override_requested_job(self):
        metadata = [{"@type": "JobPosting", "title": "Current", "url": "https://example.com/jobs", "validThrough": "2099-01-01"},
                    {"@type": "JobPosting", "title": "Related", "url": "https://example.com/old", "validThrough": "2001-01-01"}]
        result = self.checked('<script type="application/ld+json">' + json.dumps(metadata) + '</script>')
        self.assertEqual(result["evidence"]["status"], "unknown")
        self.assertEqual(result["evidence"]["title"], "Current")

    def test_ambiguous_multiple_postings_have_no_selected_expiry(self):
        metadata = [{"@type": "JobPosting", "title": "One", "validThrough": "2001-01-01"},
                    {"@type": "JobPosting", "title": "Two", "validThrough": "2001-01-02"}]
        result = self.checked('<script type="application/ld+json">' + json.dumps(metadata) + '</script>')
        self.assertEqual(result["evidence"]["status"], "unknown")
        self.assertTrue(result["evidence"]["posting_present"])
        self.assertNotIn("valid_through", result["evidence"])

    def test_login_forbidden_missing_and_throttle_are_distinct(self):
        self.assertEqual(self.checked('<form><input type="password"></form>')["evidence"]["status"], "blocked")
        for code, status in [(403, "blocked"), (429, "blocked"), (404, "closed"), (410, "closed"), (500, "unknown")]:
            self.assertEqual(self.checked("unavailable", code)["evidence"]["status"], status)

    def test_robots_disallow_prevents_page_read(self):
        with patch.object(jobs, "_resolve_public", return_value=["93.184.216.34"]), patch.object(jobs, "_request_once", return_value=response("User-agent: *\nDisallow: /jobs", 200, "text/plain")) as request:
            result = jobs.verify_source("https://example.com/jobs/1")
        self.assertEqual(result["evidence"]["status"], "robots_blocked")
        self.assertEqual(request.call_count, 1)

    def test_robots_unreadable_stops_check_instead_of_failing_open(self):
        with patch.object(jobs, "_resolve_public", return_value=["93.184.216.34"]), patch.object(jobs, "_request_once", return_value=response("<html>blocked</html>", 200, "text/html")) as request:
            result = jobs.verify_source("https://example.com/jobs/1")
        self.assertEqual(result["evidence"]["status"], "robots_unknown")
        self.assertEqual(request.call_count, 1)

    def test_redirected_page_requires_robots_permission_on_new_origin(self):
        def fake(url, addresses, **kwargs):
            if url == "https://example.com/robots.txt":
                return response("", 404, "text/plain", url)
            if url == "https://example.com/job":
                return {"url": url, "status": 302, "headers": {"location": "https://other.example/job"}, "body": b""}
            if url == "https://other.example/robots.txt":
                return response("User-agent: *\nDisallow: /", 200, "text/plain", url)
            self.fail("Redirected page must not be fetched after robots disallow")
        with patch.object(jobs, "_resolve_public", return_value=["93.184.216.34"]), patch.object(jobs, "_request_once", side_effect=fake) as request:
            result = jobs.verify_source("https://example.com/job")
        self.assertEqual(result["evidence"]["status"], "robots_blocked")
        self.assertEqual(request.call_count, 3)

    def test_timeout_is_an_uncertain_result_not_closed(self):
        with patch.object(jobs, "_fetch", side_effect=jobs.FetchError("network_error", "Sumber tidak dapat dihubungi.")):
            result = jobs.verify_source("https://example.com/jobs")
        self.assertFalse(result["ok"])
        self.assertEqual(result["evidence"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
