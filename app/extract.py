"""Extract plain text from PDF, DOCX, Excel, and plain text files. Optional OCR for scanned PDFs."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# If normal PDF text is shorter than this, try OCR when dependencies exist
_PDF_MIN_CHARS_FOR_OCR_RETRY = 80


def read_document(path: Path, try_ocr: bool = True) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        return _read_pdf(path, try_ocr=try_ocr)
    if suf == ".docx":
        return _read_docx(path)
    if suf in (".xlsx", ".xlsm"):
        return _read_xlsx(path)
    if suf == ".doc":
        return _read_doc_legacy(path)
    if suf in (".txt", ".md", ".csv", ".html", ".htm"):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    return ""


def _read_pdf(path: Path, try_ocr: bool = True) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            parts.append(t)
        text = "\n".join(parts)
        if try_ocr and len(text.strip()) < _PDF_MIN_CHARS_FOR_OCR_RETRY:
            ocr = _read_pdf_ocr(path)
            if len(ocr.strip()) > len(text.strip()):
                logger.info("OCR produced more text than digital extract: %s", path.name)
                return ocr
        return text
    except Exception:
        return ""


def _read_pdf_ocr(path: Path, max_pages: int = 12) -> str:
    """
    Optional: requires `pip install pytesseract pdf2image` and a system Tesseract install.
    See README — skipped automatically if not available.
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        return ""
    try:
        images = convert_from_path(str(path), first_page=1, last_page=max_pages)
        chunks: list[str] = []
        for im in images:
            chunks.append(pytesseract.image_to_string(im) or "")
        return "\n".join(chunks)
    except Exception as e:
        logger.debug("OCR skip for %s: %s", path.name, e)
        return ""


def _read_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        return ""
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets:
            lines.append(f"=== SHEET: {sheet.title} ===")
            for row in sheet.iter_rows(max_row=500, values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(cells):
                    lines.append("\t".join(cells))
        wb.close()
        return "\n".join(lines)
    except Exception:
        return ""


def _read_doc_legacy(path: Path) -> str:
    """Old .doc binary — try antiword if installed (optional)."""
    return ""


def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        return ""
    try:
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except Exception:
        return ""
