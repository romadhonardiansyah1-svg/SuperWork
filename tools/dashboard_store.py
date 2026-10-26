"""Local dashboard data, original CV files, and portable backups."""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4

try:
    from .dashboard_jobs import DEFAULT_SOURCES, _search_inputs
except ImportError:
    from dashboard_jobs import DEFAULT_SOURCES, _search_inputs

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL_FILES = 40 * 1024 * 1024
MAX_STATE = 50 * 1024 * 1024
MIMES = {'.txt': 'text/plain', '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (UnicodeError, RecursionError):
        raise ValueError('Data mengandung teks Unicode atau kedalaman JSON yang tidak valid.') from None


def validate_state(state):
    if not isinstance(state, dict) or len(_json_bytes(state)) > MAX_STATE:
        raise ValueError('Data ruang kerja tidak valid atau terlalu besar (maksimum 50 MB). Ekspor hasil sebelum melanjutkan pencarian besar.')
    for name in ('profile', 'filters', 'settings', 'modelTests', 'integrations', 'overrides', 'prepStages'):
        if name in state and not isinstance(state[name], dict):
            raise ValueError('Format data ruang kerja tidak sesuai: ' + name)
    for name in ('saved', 'apps', 'manualJobs', 'liveJobs', 'documents', 'activity', 'lessons', 'chat', 'interviews'):
        if name in state and (not isinstance(state[name], list) or name != 'liveJobs' and len(state[name]) > 2000):
            raise ValueError('Format/ukuran daftar ruang kerja tidak sesuai: ' + name)
    for name in ('profile', 'pendingProfile'):
        value = state.get(name)
        if value is not None and (not isinstance(value, dict) or any(not isinstance(v, str) or len(v) > 100_000 for v in value.values())):
            raise ValueError('Semua kolom profil harus berupa teks.')
    def numeric_id(value):
        return type(value) is int and 0 < value <= 2**53 - 1
    for name in ('manualJobs', 'liveJobs', 'apps', 'documents', 'interviews'):
        for item in state.get(name, []):
            if not isinstance(item, dict) or not numeric_id(item.get('id')):
                raise ValueError('ID catatan harus berupa angka positif yang valid: ' + name)
            if item.get('jobId') is not None and not numeric_id(item['jobId']):
                raise ValueError('Referensi ID lowongan tidak valid.')
    if any(not numeric_id(value) for value in state.get('saved', [])):
        raise ValueError('Daftar simpanan memiliki ID yang tidak valid.')
    if state.get('activeJobId') is not None and not numeric_id(state['activeJobId']):
        raise ValueError('ID lowongan aktif tidak valid.')
    filter_keys = {'countries', 'region', 'city', 'mode', 'eligible', 'sector', 'employment', 'level', 'sourceType', 'sourceName', 'recency', 'status'}
    filters = state.get('filters', {})
    if set(filters) - filter_keys or any(not isinstance(value, str) for key, value in filters.items() if key != 'countries'):
        raise ValueError('Filter ruang kerja tidak valid.')
    if 'countries' in filters and (not isinstance(filters['countries'], list) or len(filters['countries']) > 200 or any(not isinstance(v, str) for v in filters['countries'])):
        raise ValueError('Daftar negara pada filter tidak valid.')
    for key, value in state.get('overrides', {}).items():
        if (not isinstance(key, str) or not key.isascii() or not key.isdecimal()
                or not numeric_id(int(key)) or not isinstance(value, dict)
                or 'id' in value and value['id'] != int(key)):
            raise ValueError('Override lowongan tidak valid.')
        check = value.get('manualCheck')
        if 'manualCheck' in value:
            if (not isinstance(check, dict) or check.get('method') != 'user'
                    or check.get('status') not in ('unknown', 'open', 'closed', 'expired')
                    or not isinstance(check.get('note'), str) or not 5 <= len(check['note']) <= 1500
                    or not isinstance(check.get('checked_at'), str) or len(check['checked_at']) > 50):
                raise ValueError('Bukti pemeriksaan pengguna tidak valid.')
            try:
                datetime.fromisoformat(check['checked_at'].replace('Z', '+00:00'))
            except ValueError:
                raise ValueError('Waktu pemeriksaan pengguna tidak valid.') from None
    if 'confirmed' in state and type(state['confirmed']) is not bool:
        raise ValueError('Konfirmasi profil harus berupa boolean.')
    if state.get('cv') is not None and not isinstance(state['cv'], dict):
        raise ValueError('Format referensi CV tidak sesuai.')
    settings = state.get('settings', {})
    sources = settings.get('sources', list(DEFAULT_SOURCES))
    if not isinstance(sources, list) or len(sources) > len(DEFAULT_SOURCES) or any(value not in DEFAULT_SOURCES for value in sources):
        raise ValueError('Sumber pencarian pada pengaturan tidak valid.')
    boards = settings.get('boards', [])
    if not isinstance(boards, list) or len(boards) > 6 or any(
            not isinstance(board, dict) or board.get('type') not in ('greenhouse', 'lever')
            or not isinstance(board.get('slug'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', board['slug'])
            or 'enabled' in board and type(board['enabled']) is not bool for board in boards):
        raise ValueError('Sumber perusahaan pada pengaturan tidak valid.')
    for key in ('apiKey', 'api_key', 'token', 'password', 'credentials'):
        if key in state or key in state.get('settings', {}):
            raise ValueError('Kredensial tidak boleh disimpan di ruang kerja dashboard.')
    progress = state.get('searchProgress')
    if progress is not None:
        if not isinstance(progress, dict) or not isinstance(progress.get('scope'), dict):
            raise ValueError('Kelanjutan pencarian tidak valid.')
        scope = progress['scope']
        _, _, definitions = _search_inputs(scope.get('query'), scope.get('filters'), scope.get('sources'), scope.get('boards'))
        allowed = {source['id'] for source in definitions}
        cursors = progress.get('cursors')
        if (not isinstance(cursors, dict) or set(cursors) - allowed
                or any(not isinstance(value, str) or not 0 < len(value) <= 2048 for value in cursors.values())
                or any(not isinstance(progress.get(key), list) or len(progress[key]) > len(allowed)
                       or any(not isinstance(value, str) or value not in allowed for value in progress[key])
                       for key in ('pendingSources', 'retrySources'))
                or type(progress.get('rounds')) is not int or not 0 <= progress['rounds'] <= 2**53-1
                or any(type(progress.get(key)) is not bool for key in ('started', 'paused'))):
            raise ValueError('Kelanjutan pencarian tidak sesuai sumber yang dipilih.')
    return state


def _file_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('ID berkas tidak valid.')
    return value


def _name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 180
            or re.search(r'[\\/:\x00-\x1f\x7f\ud800-\udfff]', value) or value.startswith('.')):
        raise ValueError('Nama berkas tidak valid.')
    if Path(value).suffix.lower() not in MIMES:
        raise ValueError('Gunakan CV PDF, DOCX, atau TXT.')
    return value.strip()


def _decode(value):
    if not isinstance(value, str) or len(value) > ((MAX_FILE + 2) // 3) * 4:
        raise ValueError('Berkas melebihi batas 8 MB.')
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('Isi berkas base64 tidak valid.') from None
    if not data or len(data) > MAX_FILE:
        raise ValueError('Berkas kosong atau melebihi batas 8 MB.')
    return data


class StateConflictError(ValueError):
    def __init__(self):
        super().__init__('Data pada tab ini sudah lama atau revisinya tidak sesuai. Muat ulang dashboard sebelum menyimpan; data terbaru tetap aman.')


class DashboardStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        self.files = self.root / 'files'
        self.files.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.state_path = self.root / 'state.json'

    def _atomic(self, path, data, backup=False):
        fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if backup and path.is_file():
                self._atomic(path.with_name(path.name + '.bak'), path.read_bytes())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def load_state(self):
        return self.snapshot()['state']

    def snapshot(self):
        with self.lock:
            if not self.state_path.exists():
                return {'state': {}, 'revision': hashlib.sha256(b'').hexdigest()}
            try:
                raw = self.state_path.read_bytes()
                if len(raw) > MAX_STATE:
                    raise ValueError
                return {'state': validate_state(json.loads(raw)), 'revision': hashlib.sha256(raw).hexdigest()}
            except (ValueError, UnicodeError, TypeError):
                raise ValueError('Data lokal rusak. Jangan reset; pulihkan cadangan dari Pengaturan atau state.json.bak.') from None

    def _check_revision(self, expected_revision):
        if expected_revision is not None:
            raw = self.state_path.read_bytes() if self.state_path.exists() else b''
            if expected_revision != hashlib.sha256(raw).hexdigest():
                raise StateConflictError

    def save_state(self, state, expected_revision=None):
        validate_state(state)
        with self.lock:
            self._check_revision(expected_revision)
            self._atomic(self.state_path, _json_bytes(state), backup=True)
        return state

    def _usage(self):
        return sum(path.stat().st_size for path in self.files.iterdir() if path.suffix in ('.bin', '.json'))

    def add_file(self, name, content_base64):
        name, data = _name(name), _decode(content_base64)
        extension = Path(name).suffix.lower()
        with self.lock:
            if len(list(self.files.glob('*.bin'))) >= 2000:
                raise ValueError('Penyimpanan mencapai batas 2000 berkas. Cadangkan lalu hapus versi yang tidak diperlukan.')
            if self._usage() + len(data) > MAX_TOTAL_FILES:
                raise ValueError('Penyimpanan CV mencapai batas 40 MB. Cadangkan dan hapus versi yang tidak diperlukan.')
            file_id = uuid4().hex
            path = self.files / (file_id + '.bin')
            self._atomic(path, data)
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                result = subprocess.run([sys.executable, str(Path(__file__).with_name('dashboard_extract.py')), str(path), extension],
                                        capture_output=True, timeout=20, creationflags=flags)
                extraction = json.loads(result.stdout) if result.returncode == 0 else None
                if (not isinstance(extraction, dict) or not isinstance(extraction.get('text'), str)
                        or len(extraction['text']) > 100_000 or extraction.get('extraction_status') not in ('ready', 'needs_review', 'unavailable')):
                    raise ValueError
            except (OSError, ValueError, subprocess.TimeoutExpired):
                extraction = {'text': '', 'extraction_status': 'unavailable', 'message': 'Ekstraksi gagal atau melewati 20 detik. Berkas asli tetap tersimpan; isi profil manual atau gunakan TXT.'}
            metadata = {'id': file_id, 'name': name, 'size': len(data), 'type': MIMES[extension], 'uploaded_at': utc_now(), **extraction}
            encoded_metadata = _json_bytes(metadata)
            if self._usage() + len(encoded_metadata) > MAX_TOTAL_FILES:
                path.unlink()
                raise ValueError('CV dan teks ekstraksi melewati batas penyimpanan 40 MB. Cadangkan lalu hapus versi yang tidak diperlukan.')
            try:
                self._atomic(self.files / (file_id + '.json'), encoded_metadata)
            except OSError:
                path.unlink(missing_ok=True)
                raise
            return metadata

    def get_file(self, file_id):
        file_id = _file_id(file_id)
        with self.lock:
            path = self.files / (file_id + '.bin')
            metadata_path = self.files / (file_id + '.json')
            if not path.is_file() or not metadata_path.is_file():
                raise ValueError('Berkas CV tidak ditemukan. Pulihkan cadangan jika diperlukan.')
            metadata = json.loads(metadata_path.read_bytes())
            return metadata, path.read_bytes()

    def delete_file(self, file_id, expected_revision=None):
        file_id = _file_id(file_id)
        with self.lock:
            self._check_revision(expected_revision)
            state = self.load_state()
            updated = dict(state)
            def references_file(value):
                return isinstance(value, dict) and any(value.get(key) == file_id for key in ('id', 'key', 'fileId'))
            if references_file(state.get('cv')):
                updated.update(cv=None, pendingProfile=None, confirmed=False)
            if isinstance(state.get('cvHistory'), list):
                updated['cvHistory'] = [item for item in state['cvHistory'] if not references_file(item)]
            state_changed = updated != state
            previous_state = {}
            if state_changed:
                validate_state(updated)
                for path in (self.state_path, self.state_path.with_name(self.state_path.name + '.bak')):
                    previous_state[path] = path.read_bytes() if path.exists() else None
            staged = []
            for suffix in ('.bin', '.json'):
                original = self.files / (file_id + suffix)
                if original.exists():
                    temporary = self.files / ('.delete-' + uuid4().hex + '-' + original.name)
                    staged.append((original, temporary, original.read_bytes()))
            try:
                for original, temporary, _ in staged:
                    original.replace(temporary)
                if state_changed:
                    self.save_state(updated)
                for _, temporary, _ in staged:
                    temporary.unlink()
            except OSError:
                # Keep bytes until both deletes finish, so even a late sharing
                # violation can restore the pair before reporting failure.
                for original, temporary, data in reversed(staged):
                    if temporary.exists():
                        temporary.replace(original)
                    elif not original.exists():
                        self._atomic(original, data)
                for path, data in previous_state.items():
                    if data is None:
                        path.unlink(missing_ok=True)
                    elif not path.exists() or path.read_bytes() != data:
                        self._atomic(path, data)
                raise
            return updated

    def backup(self):
        with self.lock:
            files = []
            for metadata_path in sorted(self.files.glob('*.json')):
                metadata, data = self.get_file(metadata_path.stem)
                files.append({'metadata': metadata, 'content_base64': base64.b64encode(data).decode('ascii')})
            return {'format': 'superwork-backup', 'version': 1, 'exported_at': utc_now(), 'state': self.load_state(), 'files': files}

    def restore(self, snapshot, expected_revision=None):
        if (not isinstance(snapshot, dict) or snapshot.get('format') != 'superwork-backup'
                or snapshot.get('version') != 1 or not isinstance(snapshot.get('files'), list) or len(snapshot['files']) > 2000):
            raise ValueError('Format cadangan SuperWork tidak valid.')
        state = validate_state(snapshot.get('state'))
        prepared, seen, total = [], set(), 0
        for entry in snapshot['files']:
            if not isinstance(entry, dict) or not isinstance(entry.get('metadata'), dict):
                raise ValueError('Metadata berkas cadangan tidak valid.')
            metadata = entry['metadata']
            file_id, name = _file_id(metadata.get('id')), _name(metadata.get('name'))
            data = _decode(entry.get('content_base64'))
            total += len(data) + len(_json_bytes(metadata))
            if file_id in seen or total > MAX_TOTAL_FILES:
                raise ValueError('Isi cadangan ganda atau melewati batas penyimpanan.')
            if metadata.get('size') != len(data) or not isinstance(metadata.get('text', ''), str) or len(metadata.get('text', '')) > 100_000:
                raise ValueError('Ukuran/teks berkas cadangan tidak sesuai.')
            seen.add(file_id)
            prepared.append((file_id, {**metadata, 'name': name, 'type': MIMES[Path(name).suffix.lower()]}, data))
        with self.lock:
            self._check_revision(expected_revision)
            for file_id, _, data in prepared:
                existing = self.files / (file_id + '.bin')
                if existing.is_file() and existing.read_bytes() != data:
                    raise ValueError('ID berkas cadangan bertabrakan dengan berkas lokal. Data lama tidak diganti.')
            for file_id, metadata, _ in prepared:
                existing_metadata = self.files / (file_id + '.json')
                if existing_metadata.exists() and json.loads(existing_metadata.read_bytes()) != metadata:
                    raise ValueError('Metadata CV pada cadangan berbeda untuk ID yang sama. Berkas asli tidak diubah.')
            additional = sum(len(data) + len(_json_bytes(metadata)) for file_id, metadata, data in prepared if not (self.files / (file_id + '.bin')).exists())
            if self._usage() + additional > MAX_TOTAL_FILES:
                raise ValueError('Gabungan cadangan dan berkas lokal melewati batas 40 MB.')
            created = []
            try:
                for file_id, metadata, data in prepared:
                    for suffix, content in (('.bin', data), ('.json', _json_bytes(metadata))):
                        target = self.files / (file_id + suffix)
                        if not target.exists():
                            created.append(target)
                            self._atomic(target, content)
                self.save_state(state)
            except OSError:
                for target in created:
                    target.unlink(missing_ok=True)
                raise
        return state
