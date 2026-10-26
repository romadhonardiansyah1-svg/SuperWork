"""Application PDFs retain safe text and never execute HTML from stored drafts."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import re
import unittest

from pypdf import PdfReader

from tools.dashboard_pdf import render_document_pdf


def draft(content=None, title='Curriculum Vitae', name='CV Rani QA'):
    return {'id': 101, 'name': name, 'kind': 'cv', 'generated': {
        'title': title, 'content': content if content is not None else '# Rani QA\n\nFrontend Engineer\n\n## Keahlian\n- **Python** dan SQL\n- HTML dan CSS',
        'review_notes': ['INTERNAL_REVIEW_DO_NOT_EXPORT'], 'profile_complete': False}}


class DashboardPDFTests(unittest.TestCase):
    def read(self, document):
        filename, raw = render_document_pdf(document)
        self.assertTrue(raw.startswith(b'%PDF-'))
        self.assertTrue(filename.endswith('.pdf'))
        return filename, PdfReader(BytesIO(raw), strict=True)

    def test_plain_markdown_cv_is_a4_and_excludes_internal_notes(self):
        filename, reader = self.read(draft())
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertEqual(filename, 'CV Rani QA.pdf')
        self.assertIn('Curriculum Vitae', text)
        self.assertIn('Rani QA', text)
        self.assertIn('Python dan SQL', text)
        self.assertNotIn('**', text)
        self.assertNotIn('INTERNAL_REVIEW_DO_NOT_EXPORT', text)
        self.assertNotIn('DRAF AI', text)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.2756, places=2)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 841.8898, places=2)

    def test_cover_letter_and_unicode_accents_retain_original_text(self):
        document = draft('José Márquez\nSão Paulo, Brasil\n\nYth. François Müller\n\nSaya mengembangkan antarmuka café untuk pengguna.',
                         title='Surat lamaran Mélanie', name='Surat lamaran Mélanie')
        document['kind'] = 'cover'
        _, reader = self.read(document)
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for value in ('José Márquez', 'São Paulo', 'François Müller', 'café', 'Surat lamaran Mélanie'):
            self.assertIn(value, text)

    def test_html_and_remote_image_strings_stay_literal_without_active_actions(self):
        content = '<img src="https://example.test/private.png"/>\n<script>alert("x")</script>\n<a href="file:///C:/private.txt">Contact</a>'
        _, reader = self.read(draft(content))
        text = ''.join(page.extract_text() for page in reader.pages)
        self.assertIn('<img src=', text)
        self.assertIn('<script>', text)
        self.assertIn('file:///C:/private.txt', text)
        self.assertNotIn('/OpenAction', reader.trailer['/Root'])
        self.assertTrue(all(not page.get('/Annots') for page in reader.pages))
        self.assertTrue(all(not list(page.images) for page in reader.pages))

    def test_long_word_wraps_and_retains_every_character(self):
        word = 'LONGTOKEN_' + 'a' * 4000 + '_ENDTOKEN'
        _, reader = self.read(draft(word))
        text = ''.join(page.extract_text().replace(f'Halaman {index}', '')
                       for index, page in enumerate(reader.pages, 1))
        compact = re.sub(r'\s+', '', text)
        self.assertIn(word, compact)

    def test_multipage_content_retains_first_last_and_all_paragraphs(self):
        content = '\n\n'.join(f'MARKER_{index:03d} ' + 'Pengalaman proyek sintetis yang dapat ditinjau. ' * 12 for index in range(80))
        _, reader = self.read(draft(content))
        self.assertGreater(len(reader.pages), 2)
        text = '\n'.join(page.extract_text() for page in reader.pages)
        for index in range(80):
            self.assertIn(f'MARKER_{index:03d}', text)
        for index, page in enumerate(reader.pages, 1):
            self.assertIn(f'Halaman {index}', page.extract_text())

    def test_missing_empty_oversized_and_invalid_draft_content_is_rejected(self):
        for document in ({}, {'generated': {}}, draft(''), draft(' '), draft('x' * 60_001),
                         draft('\x00invalid'), draft(title='x' * 241)):
            with self.subTest(document_keys=list(document)), self.assertRaises(ValueError):
                render_document_pdf(document)

    def test_filename_cannot_inject_headers_or_paths(self):
        filename, _ = self.read(draft(name='CV ../../Mélanie\r\nContent-Type: text/html'))
        self.assertNotRegex(filename, r'[\\/\r\n:]')
        self.assertLessEqual(len(filename), 184)

    def test_concurrent_pdf_requests_keep_their_own_document_text(self):
        def generate(index):
            _, reader = self.read(draft(f'UNIQUE_DOCUMENT_{index}'))
            return ''.join(page.extract_text() for page in reader.pages)
        with ThreadPoolExecutor(max_workers=4) as pool:
            output = list(pool.map(generate, range(4)))
        for index, text in enumerate(output):
            self.assertIn(f'UNIQUE_DOCUMENT_{index}', text)
            self.assertNotIn(f'UNIQUE_DOCUMENT_{(index + 1) % 4}', text)


if __name__ == '__main__':
    unittest.main()
