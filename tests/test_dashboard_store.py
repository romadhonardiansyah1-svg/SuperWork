import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from tools.dashboard_store import DashboardStore


class DashboardStoreTests(unittest.TestCase):
    def test_paginated_results_and_continuation_survive_reopen_beyond_old_count_limit(self):
        progress = {'scope': {'query': 'admin', 'filters': {}, 'sources': ['jobstreet'], 'boards': []},
                    'cursors': {'jobstreet': 'https://id.jobstreet.com/admin-jobs?page=72'},
                    'pendingSources': ['jobstreet'], 'retrySources': [], 'rounds': 71,
                    'started': True, 'paused': True}
        state = {'liveJobs': [{'id': n, 'description': 'a' * 1600} for n in range(1, 2102)],
                 'searchProgress': progress, 'profile': {'name': 'Preserved'}}
        self.store.save_state(state)
        self.assertEqual(DashboardStore(self.store.root).load_state(), state)

    def test_malformed_search_progress_cannot_replace_saved_data(self):
        self.store.save_state({'profile': {'name': 'Preserved'}})
        for progress in ('wrong', {'scope': {}}, {'scope': {'query': 'admin', 'filters': {},
                'sources': ['jobstreet'], 'boards': []}, 'cursors': {'unknown': 'https://evil.example/'},
                'pendingSources': ['unknown'], 'retrySources': [], 'rounds': 0, 'started': True, 'paused': True}):
            with self.subTest(progress=progress), self.assertRaises(ValueError):
                self.store.save_state({'searchProgress': progress})
            self.assertEqual(self.store.load_state()['profile']['name'], 'Preserved')

    def test_indonesia_source_preferences_survive_reopen_and_empty_stays_empty(self):
        self.store.save_state({'settings': {'sources': ['kalibrr', 'jobstreet', 'glints']}})
        self.assertEqual(DashboardStore(self.store.root).load_state()['settings']['sources'], ['kalibrr', 'jobstreet', 'glints'])
        self.store.save_state({'settings': {'sources': []}})
        self.assertEqual(DashboardStore(self.store.root).load_state()['settings']['sources'], [])

    def test_manual_source_check_survives_reopen_and_malformed_checks_do_not_replace_it(self):
        check = {'method':'user', 'status':'closed', 'note':'Tertulis posisi ditutup pada sumber.', 'checked_at':'2026-10-25T00:00:00Z'}
        state = {'overrides': {'123': {'status':'closed', 'checkedAt':check['checked_at'], 'manualCheck':check}}}
        self.store.save_state(state)
        for invalid in [None, {**check, 'method':'automatic'}, {**check, 'status':'verified'}, {**check, 'note':{}}, {**check, 'checked_at':'not-a-date'}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.save_state({'overrides': {'123': {'manualCheck':invalid}}})
            self.assertEqual(DashboardStore(self.store.root).load_state(), state)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DashboardStore(Path(self.temp.name))

    def test_state_survives_new_instance_and_previous_copy_exists(self):
        self.store.save_state({'version': 2, 'profile': {'name': 'Kandidat Uji'}})
        self.store.save_state({'version': 2, 'profile': {'name': 'Revisi Uji'}})
        self.assertEqual(DashboardStore(Path(self.temp.name)).load_state()['profile']['name'], 'Revisi Uji')
        self.assertEqual(json.loads((Path(self.temp.name) / 'state.json.bak').read_text())['profile']['name'], 'Kandidat Uji')

    def test_invalid_state_cannot_replace_existing_data(self):
        self.store.save_state({'profile': {'name': 'Tersimpan'}})
        with self.assertRaises(ValueError):
            self.store.save_state({'profile': 'wrong type'})
        self.assertEqual(self.store.load_state()['profile']['name'], 'Tersimpan')

    def test_snapshot_hashes_exact_saved_bytes_and_survives_reopen(self):
        self.assertEqual(self.store.snapshot(), {'state': {}, 'revision': hashlib.sha256(b'').hexdigest()})
        raw = b'{ "profile": {"name": "Fixture"} }\n'
        self.store.state_path.write_bytes(raw)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot, {'state': {'profile': {'name': 'Fixture'}}, 'revision': hashlib.sha256(raw).hexdigest()})
        self.assertEqual(DashboardStore(self.store.root).snapshot(), snapshot)
        self.store.state_path.write_bytes(b'{"profile":{"name":"Fixture"}}')
        self.assertEqual(self.store.snapshot()['state'], snapshot['state'])
        self.assertNotEqual(self.store.snapshot()['revision'], snapshot['revision'])

    def test_stale_save_preserves_state_backup_and_original_files(self):
        self.store.save_state({'profile': {'name': 'Awal'}})
        revision = hashlib.sha256(self.store.state_path.read_bytes()).hexdigest()
        state = {'profile': {'name': 'Terbaru'}}
        self.assertEqual(self.store.save_state(state, expected_revision=revision), state)
        (self.store.files / ('a' * 32 + '.bin')).write_bytes(b'Original fixture')
        before = {str(path.relative_to(self.store.root)): path.read_bytes()
                  for path in self.store.root.rglob('*') if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'tab') as failure:
            self.store.save_state({'profile': {'name': 'Tab lama'}}, expected_revision=revision)
        self.assertEqual(type(failure.exception).__name__, 'StateConflictError')
        self.assertEqual({str(path.relative_to(self.store.root)): path.read_bytes()
                          for path in self.store.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.store.load_state(), state)

    def test_concurrent_saves_from_same_revision_have_one_winner(self):
        revision = hashlib.sha256(b'').hexdigest()
        barrier = threading.Barrier(2)
        outcomes = []
        def write(name):
            barrier.wait(timeout=5)
            try:
                self.store.save_state({'profile': {'name': name}}, expected_revision=revision)
                outcomes.append(('saved', name))
            except ValueError as exc:
                outcomes.append((type(exc).__name__, name))
        threads = [threading.Thread(target=write, args=(name,)) for name in ('Tab A', 'Tab B')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual([result for result, _ in outcomes], ['saved', 'StateConflictError'])
        winner = next(name for result, name in outcomes if result == 'saved')
        self.assertEqual(self.store.load_state()['profile']['name'], winner)
        self.assertFalse(self.store.state_path.with_suffix('.json.bak').exists())

    def test_restore_checks_revision_after_preparation_before_any_file_write(self):
        self.store.save_state({'profile': {'name': 'Awal'}})
        revision = hashlib.sha256(self.store.state_path.read_bytes()).hexdigest()
        snapshot = {'format': 'superwork-backup', 'version': 1, 'state': {'profile': {'name': 'Cadangan'}},
                    'files': [{'metadata': {'id': 'a' * 32, 'name': 'CV-fixture.txt', 'size': 3, 'text': 'Uji'},
                               'content_base64': 'VWpp'}]}
        before = {}
        def decode_while_another_tab_saves(value):
            self.store.save_state({'profile': {'name': 'Terbaru'}})
            before.update({str(path.relative_to(self.store.root)): path.read_bytes()
                           for path in self.store.root.rglob('*') if path.is_file()})
            return base64.b64decode(value)
        with patch('tools.dashboard_store._decode', side_effect=decode_while_another_tab_saves), self.assertRaisesRegex(ValueError, 'tab') as failure:
            self.store.restore(snapshot, expected_revision=revision)
        self.assertEqual(type(failure.exception).__name__, 'StateConflictError')
        self.assertTrue(before)
        self.assertEqual({str(path.relative_to(self.store.root)): path.read_bytes()
                          for path in self.store.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.store.load_state()['profile']['name'], 'Terbaru')

    def test_guarded_restore_succeeds_with_current_revision(self):
        self.store.save_state({'profile': {'name': 'Awal'}})
        revision = hashlib.sha256(self.store.state_path.read_bytes()).hexdigest()
        state = {'profile': {'name': 'Cadangan'}}
        snapshot = {'format': 'superwork-backup', 'version': 1, 'state': state, 'files': []}
        self.assertEqual(self.store.restore(snapshot, expected_revision=revision), state)
        self.assertEqual(self.store.snapshot()['state'], state)
        self.assertNotEqual(self.store.snapshot()['revision'], revision)

    def test_source_preferences_and_practice_feedback_survive_reopen(self):
        state = {'settings': {'sources': [], 'boards': [{'type': 'greenhouse', 'slug': 'company', 'enabled': False}]},
                 'aiResults': {'response:learning:101:0': {'answer': 'Hasil latihan asli.', 'feedback': {'summary': 'Masukan.'}}}}
        self.store.save_state(state)
        reopened = DashboardStore(self.store.root).load_state()
        self.assertEqual(reopened, state)
        self.assertEqual(self.store.backup()['state'], state)

    def test_invalid_source_preferences_do_not_replace_existing_state(self):
        self.store.save_state({'settings': {'sources': ['arbeitnow']}})
        cases = [{'sources': ['unknown']}, {'sources': 'freehire'}, {'sources': [False]},
                 {'boards': [{'type': 'lever', 'slug': '../secret'}]},
                 {'boards': [{'type': 'lever', 'slug': 'company', 'enabled': 'false'}]},
                 {'boards': [{'type': 'lever', 'slug': str(i)} for i in range(7)]}]
        for settings in cases:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.store.save_state({'settings': settings})
        self.assertEqual(self.store.load_state()['settings']['sources'], ['arbeitnow'])

    def test_nested_profile_and_record_ids_are_validated_for_restore(self):
        cases = [{'profile': {'fullName': {'html': '<script>'}}}, {'manualJobs': [{'id': '1" onclick="attack'}]},
                 {'documents': [{'id': True}]}, {'filters': {'unexpected-key': 'x'}}, {'overrides': {'bad-key': {}}}]
        for state in cases:
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.store.save_state(state)

    def test_txt_upload_backup_and_restore_preserve_original(self):
        raw = 'Nama: Kandidat Uji\nKeahlian: Python'.encode()
        file = self.store.add_file('CV-uji.txt', base64.b64encode(raw).decode())
        self.assertEqual(file['extraction_status'], 'ready')
        self.assertIn('Kandidat Uji', file['text'])
        self.store.save_state({'cv': {'id': file['id']}, 'profile': {}})
        backup = self.store.backup()
        restored = DashboardStore(Path(self.temp.name) / 'restored')
        restored.restore(backup)
        metadata, data = restored.get_file(file['id'])
        self.assertEqual(metadata['name'], 'CV-uji.txt')
        self.assertEqual(data, raw)

    def test_bad_file_names_extensions_ids_and_base64_rejected(self):
        for name in ('../CV.txt', 'C:\\secret.txt', '.env', 'payload.html', 'CV.exe'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.add_file(name, 'eA==')
        with self.assertRaises(ValueError):
            self.store.add_file('CV.txt', '!notbase64')
        with self.assertRaises(ValueError):
            self.store.get_file('../state.json')

    def test_docx_extracts_only_text_not_markup(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Nama: Uji DOCX</w:t></w:r></w:p></w:body></w:document>')
        file = self.store.add_file('CV.docx', base64.b64encode(buffer.getvalue()).decode())
        self.assertEqual(file['extraction_status'], 'ready')
        self.assertIn('Uji DOCX', file['text'])

    def test_corrupt_backup_is_rejected_without_replacing_state(self):
        self.store.save_state({'profile': {'name': 'Tetap'}})
        with self.assertRaises(ValueError):
            self.store.restore({'format': 'superwork-backup', 'version': 1, 'state': {}, 'files': [{'id': '../oops'}]})
        self.assertEqual(self.store.load_state()['profile']['name'], 'Tetap')

    def test_corrupt_state_does_not_silently_reset(self):
        (Path(self.temp.name) / 'state.json').write_text('{broken')
        with self.assertRaises(ValueError):
            self.store.load_state()

    def test_restore_state_write_failure_keeps_existing_file_metadata_and_state(self):
        file = self.store.add_file('CV-awal.txt', 'VWpp')
        self.store.save_state({'profile': {'name': 'Awal'}})
        snapshot = self.store.backup()
        snapshot['state']['profile']['name'] = 'Baru'
        snapshot['files'][0]['metadata']['name'] = 'CV-diganti.txt'
        with self.assertRaises(ValueError):
            self.store.restore(snapshot)
        self.assertEqual(self.store.get_file(file['id'])[0]['name'], 'CV-awal.txt')
        self.assertEqual(self.store.load_state()['profile']['name'], 'Awal')

        snapshot = self.store.backup()
        new_file = {**snapshot['files'][0], 'metadata': {**file, 'id': 'a' * 32}}
        snapshot['files'].append(new_file)
        original_atomic = self.store._atomic
        def fail_state_write(path, data, backup=False):
            if path == self.store.state_path:
                raise OSError('fixture disk failure')
            return original_atomic(path, data, backup)
        with patch.object(self.store, '_atomic', side_effect=fail_state_write), self.assertRaises(OSError):
            self.store.restore(snapshot)
        self.assertFalse((self.store.files / ('a' * 32 + '.bin')).exists())
        self.assertEqual(self.store.load_state()['profile']['name'], 'Awal')

    def test_more_than_forty_small_uploads_remain_restorable(self):
        extraction = type('Result', (), {'returncode': 0, 'stdout': b'{"text":"Uji","extraction_status":"ready"}'})()
        with patch('tools.dashboard_store.subprocess.run', return_value=extraction):
            for i in range(41):
                self.store.add_file('CV-uji.txt', 'VWpp')
        restored = DashboardStore(Path(self.temp.name) / 'restore-many')
        restored.restore(self.store.backup())
        self.assertEqual(len(restored.backup()['files']), 41)

    def test_extraction_metadata_counts_toward_restorable_backup_limit(self):
        extraction = type('Result', (), {'returncode': 0, 'stdout': json.dumps({'text': 'x' * 3000, 'extraction_status': 'ready'}).encode()})()
        with patch('tools.dashboard_store.MAX_TOTAL_FILES', 2048), patch('tools.dashboard_store.subprocess.run', return_value=extraction), self.assertRaises(ValueError):
            self.store.add_file('CV.txt', 'VWpp')
        self.assertEqual(list(self.store.files.iterdir()), [])

    def test_metadata_write_failure_rolls_back_new_original_file(self):
        original = self.store._atomic
        def fail_metadata(path, data, backup=False):
            if path.suffix == '.json':
                raise OSError('Fixture disk full')
            return original(path, data, backup)
        with patch.object(self.store, '_atomic', side_effect=fail_metadata), self.assertRaises(OSError):
            self.store.add_file('CV.txt', 'VWpp')
        self.assertEqual(list(self.store.files.iterdir()), [])

    def test_failed_metadata_delete_preserves_original_metadata_state_and_backup(self):
        file = self.store.add_file('CV-delete-uji.txt', 'TmFtYTogS2FuZGlkYXQgVWpp')
        self.store.save_state({'cv': {'id': file['id']}, 'profile': {'fullName': 'Kandidat Uji'}})
        before_state = self.store.load_state()
        before_metadata, before_bytes = self.store.get_file(file['id'])
        original_unlink = Path.unlink
        def fail_metadata_unlink(path, *args, **kwargs):
            if path.suffix == '.json':
                raise PermissionError('Fixture metadata sharing violation')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_metadata_unlink), self.assertRaises(PermissionError):
            self.store.delete_file(file['id'])
        metadata, raw = self.store.get_file(file['id'])
        self.assertEqual(metadata, before_metadata)
        self.assertEqual(raw, before_bytes)
        self.assertEqual(self.store.load_state(), before_state)
        self.assertEqual(self.store.backup()['files'][0]['content_base64'], base64.b64encode(before_bytes).decode())
        self.assertEqual(len(list(self.store.files.iterdir())), 2)

    def test_failed_second_delete_move_rolls_back_first_without_changing_state(self):
        file = self.store.add_file('CV-move-uji.txt', 'TmFtYTogS2FuZGlkYXQgVWpp')
        self.store.save_state({'cv': {'id': file['id']}, 'profile': {'fullName': 'Kandidat Uji'}})
        before_state = self.store.load_state()
        before_metadata, before_bytes = self.store.get_file(file['id'])
        metadata_path = self.store.files / (file['id'] + '.json')
        original_replace = Path.replace
        def fail_metadata_move(path, target):
            if path == metadata_path:
                raise PermissionError('Fixture second rename sharing violation')
            return original_replace(path, target)
        with patch.object(Path, 'replace', fail_metadata_move), self.assertRaises(PermissionError):
            self.store.delete_file(file['id'])
        metadata, raw = self.store.get_file(file['id'])
        self.assertEqual(metadata, before_metadata)
        self.assertEqual(raw, before_bytes)
        self.assertEqual(self.store.load_state(), before_state)
        self.assertEqual(len(self.store.backup()['files']), 1)
        self.assertEqual(len(list(self.store.files.iterdir())), 2)

    def test_successful_delete_removes_both_files_and_is_idempotent(self):
        file = self.store.add_file('CV-delete-sukses.txt', 'VWpp')
        self.store.delete_file(file['id'])
        self.assertEqual(list(self.store.files.iterdir()), [])
        self.store.delete_file(file['id'])
        self.assertEqual(list(self.store.files.iterdir()), [])

    def test_stale_delete_preserves_newer_state_backup_and_file_pair(self):
        file = self.store.add_file('CV-delete-tab-lama.txt', 'VWpp')
        state = {'cv': {'id': file['id']}, 'profile': {'name': 'Awal'}}
        self.store.save_state(state)
        revision = self.store.snapshot()['revision']
        newer = {**state, 'profile': {'name': 'Tab B'}}
        self.store.save_state(newer, expected_revision=revision)
        before = {str(path.relative_to(self.store.root)): path.read_bytes()
                  for path in self.store.root.rglob('*') if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'tab') as failure:
            self.store.delete_file(file['id'], expected_revision=revision)
        self.assertEqual(type(failure.exception).__name__, 'StateConflictError')
        self.assertEqual({str(path.relative_to(self.store.root)): path.read_bytes()
                          for path in self.store.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.store.load_state(), newer)

    def test_deleting_active_cv_cleans_all_matching_references_and_preserves_work(self):
        file = self.store.add_file('CV-aktif.txt', 'VWpp')
        state = {'cv': {'id': file['id'], 'key': file['id']}, 'pendingProfile': {'name': 'Impor'}, 'confirmed': True,
                 'cvHistory': [{'id': file['id']}, {'key': file['id']}, {'fileId': file['id']}, {'id': 'b' * 32}],
                 'profile': {'name': 'Tetap'}, 'apps': [{'id': 1}], 'documents': [{'id': 2}], 'interviews': [{'id': 3}]}
        self.store.save_state(state)
        self.store.delete_file(file['id'], expected_revision=self.store.snapshot()['revision'])
        expected = {**state, 'cv': None, 'pendingProfile': None, 'confirmed': False, 'cvHistory': [{'id': 'b' * 32}]}
        self.assertEqual(self.store.load_state(), expected)
        self.assertEqual(json.loads(self.store.state_path.with_suffix('.json.bak').read_bytes()), state)
        self.assertEqual(list(self.store.files.iterdir()), [])

    def test_deleting_history_preserves_active_cv_profile_and_confirmation(self):
        for reference_key in ('id', 'key', 'fileId'):
            with self.subTest(reference_key=reference_key):
                file = self.store.add_file('CV-riwayat.txt', 'VWpp')
                state = {'cv': {'id': 'b' * 32, 'key': 'b' * 32}, 'pendingProfile': {'name': 'Impor'}, 'confirmed': True,
                         'cvHistory': [{reference_key: file['id']}, {'key': 'c' * 32}], 'profile': {'name': 'Tetap'}}
                self.store.save_state(state)
                self.store.delete_file(file['id'], expected_revision=self.store.snapshot()['revision'])
                self.assertEqual(self.store.load_state(), {**state, 'cvHistory': [{'key': 'c' * 32}]})
                self.assertEqual(list(self.store.files.iterdir()), [])

    def test_delete_second_unlink_failure_rolls_back_new_state_backup_and_originals(self):
        file = self.store.add_file('CV-rollback.txt', 'VWpp')
        self.store.save_state({'profile': {'name': 'Sebelum CV'}})
        self.store.save_state({'cv': {'key': file['id']}, 'pendingProfile': {'name': 'Impor'}, 'confirmed': True,
                               'profile': {'name': 'Terbaru'}})
        before = {str(path.relative_to(self.store.root)): path.read_bytes()
                  for path in self.store.root.rglob('*') if path.is_file()}
        original_unlink = Path.unlink
        observed = []
        def fail_second_unlink(path, *args, **kwargs):
            if path.name.startswith('.delete-') and path.suffix == '.json':
                observed.append(self.store.load_state())
                raise PermissionError('Fixture metadata sharing violation after state write')
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_second_unlink), self.assertRaises(PermissionError):
            self.store.delete_file(file['id'])
        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0].get('cv'))
        self.assertEqual({str(path.relative_to(self.store.root)): path.read_bytes()
                          for path in self.store.root.rglob('*') if path.is_file()}, before)

    def test_delete_state_replace_failure_restores_prior_backup_and_file_pair(self):
        file = self.store.add_file('CV-gagal-state.txt', 'VWpp')
        self.store.save_state({'profile': {'name': 'Sebelum CV'}})
        self.store.save_state({'cv': {'fileId': file['id']}, 'confirmed': True})
        before = {str(path.relative_to(self.store.root)): path.read_bytes()
                  for path in self.store.root.rglob('*') if path.is_file()}
        from tools.dashboard_store import os
        original_replace = os.replace
        def fail_state_replace(source, target):
            if Path(target) == self.store.state_path:
                raise PermissionError('Fixture state replace failure after backup write')
            return original_replace(source, target)
        with patch('tools.dashboard_store.os.replace', side_effect=fail_state_replace), self.assertRaises(PermissionError):
            self.store.delete_file(file['id'])
        self.assertEqual({str(path.relative_to(self.store.root)): path.read_bytes()
                          for path in self.store.root.rglob('*') if path.is_file()}, before)

    def test_delete_error_after_state_write_rolls_back_even_without_original_backup(self):
        file = self.store.add_file('CV-gagal-setelah-state.txt', 'VWpp')
        self.store.save_state({'cv': {'id': file['id']}, 'confirmed': True})
        before = {str(path.relative_to(self.store.root)): path.read_bytes()
                  for path in self.store.root.rglob('*') if path.is_file()}
        original_atomic = self.store._atomic
        failed = False
        def fail_after_state_write(path, data, backup=False):
            nonlocal failed
            result = original_atomic(path, data, backup)
            if path == self.store.state_path and not failed:
                failed = True
                raise OSError('Fixture error after replacing state')
            return result
        with patch.object(self.store, '_atomic', side_effect=fail_after_state_write), self.assertRaises(OSError):
            self.store.delete_file(file['id'])
        self.assertTrue(failed)
        self.assertEqual({str(path.relative_to(self.store.root)): path.read_bytes()
                          for path in self.store.root.rglob('*') if path.is_file()}, before)

    def test_unreferenced_delete_does_not_rewrite_state_revision_or_backup(self):
        file = self.store.add_file('CV-tidak-direferensikan.txt', 'VWpp')
        self.store.save_state({'profile': {'name': 'Cadangan'}})
        self.store.save_state({'profile': {'name': 'Terbaru'}})
        raw = b'{ "profile": { "name": "Terbaru" } }\n'
        self.store.state_path.write_bytes(raw)
        revision = self.store.snapshot()['revision']
        backup = self.store.state_path.with_suffix('.json.bak').read_bytes()
        self.store.delete_file(file['id'], expected_revision=revision)
        self.store.delete_file(file['id'], expected_revision=revision)
        self.assertEqual(self.store.snapshot()['revision'], revision)
        self.assertEqual(self.store.state_path.read_bytes(), raw)
        self.assertEqual(self.store.state_path.with_suffix('.json.bak').read_bytes(), backup)
        self.assertEqual(list(self.store.files.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
