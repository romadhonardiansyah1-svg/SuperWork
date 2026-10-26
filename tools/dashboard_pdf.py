"""Export a stored application draft as a bounded, text-only A4 PDF."""
from io import BytesIO
from pathlib import Path
import re
import threading
import unicodedata
from xml.sax.saxutils import escape

FONT_DIR = Path(__file__).resolve().parent.parent / 'cover_letters' / 'OpenFonts' / 'fonts' / 'lato'
_PDF_LOCK = threading.Lock()
_INVALID_TEXT = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]')


def _inline(value):
    # Escape first; the only markup we introduce ourselves is paired bold text.
    return re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', escape(value))


def render_document_pdf(document):
    """Return (safe download filename, PDF bytes), without file/URL input tools."""
    generated = document.get('generated') if isinstance(document, dict) else None
    content = generated.get('content') if isinstance(generated, dict) else None
    if not isinstance(content, str) or not content.strip() or len(content) > 60_000:
        raise ValueError('Isi dokumen belum tersedia atau terlalu panjang. Buat ulang dokumen sebelum mengunduh PDF.')
    title = generated.get('title') or document.get('name') or 'Dokumen lamaran'
    if not isinstance(title, str) or not title.strip() or len(title) > 240 or _INVALID_TEXT.search(title + content):
        raise ValueError('Judul atau teks dokumen tidak valid untuk PDF. Tinjau dan buat ulang dokumen.')
    title = unicodedata.normalize('NFC', title.strip())
    content = unicodedata.normalize('NFC', content.replace('\r\n', '\n').replace('\r', '\n').strip())
    name = document.get('name') if isinstance(document.get('name'), str) else title
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f\ud800-\udfff]', '-', name)
    filename = re.sub(r'\s+', ' ', filename).strip(' .')[:180].strip(' .') or 'Dokumen lamaran'
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]', filename, re.IGNORECASE):
        filename = 'Dokumen-' + filename
    filename += '.pdf'
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont, TTFError
        from reportlab.platypus import Paragraph, SimpleDocTemplate
        from reportlab.platypus.doctemplate import LayoutError
    except ImportError:
        raise ValueError('Runtime PDF belum tersedia. Buka SuperWork memakai shortcut dengan runtime proyek .venv.') from None
    with _PDF_LOCK:
        try:
            registered = pdfmetrics.getRegisteredFontNames()
            for font, file in (('SuperWorkLato', 'Lato-Reg.ttf'), ('SuperWorkLato-Bold', 'Lato-Bol.ttf')):
                if font not in registered:
                    pdfmetrics.registerFont(TTFont(font, str(FONT_DIR / file)))
            pdfmetrics.registerFontFamily('SuperWorkLato', normal='SuperWorkLato', bold='SuperWorkLato-Bold',
                                          italic='SuperWorkLato', boldItalic='SuperWorkLato-Bold')
            body = ParagraphStyle('Body', fontName='SuperWorkLato', fontSize=10.6, leading=15.5,
                                  textColor=HexColor('#202b30'), spaceAfter=9, splitLongWords=1)
            heading_styles = {level: ParagraphStyle('Heading' + str(level), parent=body,
                fontName='SuperWorkLato-Bold', fontSize={1: 14, 2: 12, 3: 11}[level],
                leading={1: 19, 2: 17, 3: 16}[level], spaceBefore=10, spaceAfter=6, keepWithNext=True)
                for level in (1, 2, 3)}
            title_style = ParagraphStyle('Title', parent=body, fontName='SuperWorkLato-Bold',
                                         fontSize=19, leading=25, spaceAfter=18, keepWithNext=True)
            bullet_style = ParagraphStyle('Bullet', parent=body, leftIndent=14, bulletIndent=0,
                                          bulletFontName='SuperWorkLato', bulletFontSize=10.6, spaceAfter=5)
            story = [Paragraph(escape(title), title_style)]
            pending = []
            def flush():
                if pending:
                    story.append(Paragraph('<br/>'.join(_inline(line) for line in pending), body))
                    pending.clear()
            for raw_line in content.split('\n'):
                line = raw_line.strip()
                heading = re.fullmatch(r'(#{1,3})\s+(.+)', line)
                bullet = re.fullmatch(r'(?:([-*•])|(\d{1,2}[.)]))\s+(.+)', line)
                if not line:
                    flush()
                elif heading and len(heading[2]) <= 500:
                    flush()
                    story.append(Paragraph(_inline(heading[2]), heading_styles[len(heading[1])]))
                elif bullet:
                    flush()
                    story.append(Paragraph(_inline(bullet[3]), bullet_style, bulletText=bullet[2] or '•'))
                else:
                    pending.append(line)
            flush()
            output = BytesIO()
            template = SimpleDocTemplate(output, pagesize=A4, leftMargin=52, rightMargin=52,
                                         topMargin=48, bottomMargin=50, title=title, author='')
            def footer(canvas, doc):
                canvas.saveState()
                canvas.setFont('SuperWorkLato', 8)
                canvas.setFillColor(HexColor('#657078'))
                canvas.drawRightString(A4[0] - 52, 27, f'Halaman {doc.page}')
                canvas.restoreState()
            template.build(story, onFirstPage=footer, onLaterPages=footer)
            return filename, output.getvalue()
        except (OSError, ValueError, LayoutError, TTFError):
            raise ValueError('PDF belum dapat dibuat. Periksa font lokal dan isi dokumen; tidak ada berkas yang diubah.') from None
