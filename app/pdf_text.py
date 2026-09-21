"""Run PDF parsing in a disposable subprocess with time/memory bounds."""
import json
import sys
from pypdf import PdfReader


def extract(path):
    reader = PdfReader(path)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 150:
        raise ValueError('PDF_ENCRYPTED_OR_TOO_MANY_PAGES')
    pages = []
    total = 0
    for page in reader.pages:
        # Avoid decompressing huge content streams in the parent worker.
        text = page.extract_text(extraction_mode='layout') or ''
        total += len(text)
        if total > 180000:
            raise ValueError('PDF_TEXT_TOO_LONG')
        pages.append(text)
    if sum(len(p.strip()) for p in pages) < 300:
        raise ValueError('PDF_REQUIRES_OCR')
    return pages


if __name__ == '__main__':
    try:
        if sys.platform != 'win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
        print(json.dumps(extract(sys.argv[1]), ensure_ascii=True))
    except Exception:
        raise SystemExit(2)
