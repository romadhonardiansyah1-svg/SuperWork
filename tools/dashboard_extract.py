"""Extract uploaded CV text in a bounded child process, without network access."""
import io
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

MAX_TEXT = 100_000


def extract(data, extension):
    if extension == '.txt':
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            return {'text': '', 'extraction_status': 'unavailable', 'message': 'Simpan TXT dengan encoding UTF-8 lalu unggah ulang.'}
    elif extension == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(x.file_size for x in entries) > 20_000_000:
                raise ValueError('Dokumen terlalu kompleks untuk diekstrak.')
            info = archive.getinfo('word/document.xml')
            if info.file_size > 4_000_000 or info.file_size > max(info.compress_size, 1) * 200:
                raise ValueError('Isi DOCX melewati batas aman ekstraksi.')
            xml = archive.read(info)
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError('Deklarasi eksternal pada DOCX tidak didukung.')
            document = ET.fromstring(xml)
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            text = '\n'.join(''.join(p.itertext()) for p in document.iter(ns + 'p'))
    elif extension == '.pdf':
        from pypdf import PdfReader
        if not data.startswith(b'%PDF-'):
            raise ValueError('Berkas bukan PDF yang dikenali.')
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            return {'text': '', 'extraction_status': 'unavailable', 'message': 'PDF terkunci. Unggah salinan tanpa kata sandi.'}
        if len(reader.pages) > 25:
            raise ValueError('Ekstraksi CV dibatasi 25 halaman.')
        chunks = []
        for page in reader.pages:
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 5_000_000:
                raise ValueError('Halaman PDF terlalu kompleks untuk diekstrak.')
            chunks.append(page.extract_text() or '')
            if sum(map(len, chunks)) > MAX_TEXT:
                raise ValueError('Teks CV melewati batas ekstraksi.')
        text = '\n'.join(chunks)
    else:
        raise ValueError('Jenis dokumen tidak didukung.')
    if len(text) > MAX_TEXT:
        raise ValueError('Teks CV melewati batas ekstraksi.')
    text = text.replace('\x00', '').strip()
    return {'text': text, 'extraction_status': 'ready' if text else 'needs_review',
            'message': 'Periksa hasil ekstraksi sebelum mengonfirmasi profil.' if text else 'Tidak ada teks terbaca. PDF hasil pindai memerlukan teks/OCR dari Anda.'}


if __name__ == '__main__':
    try:
        result = extract(Path(sys.argv[1]).read_bytes(), sys.argv[2])
    except ImportError:
        result = {'text': '', 'extraction_status': 'unavailable', 'message': 'pypdf belum tersedia. Berkas asli tetap tersimpan; unggah TXT/DOCX atau isi profil manual.'}
    except Exception:
        # The child handles malformed third-party documents; preserve the original with an explicit failure.
        result = {'text': '', 'extraction_status': 'unavailable', 'message': 'Dokumen tidak dapat diekstrak dengan aman. Berkas asli tersimpan; gunakan TXT/DOCX atau isi profil manual.'}
    print(json.dumps(result, ensure_ascii=True))
