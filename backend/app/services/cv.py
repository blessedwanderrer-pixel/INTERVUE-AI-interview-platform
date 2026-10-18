import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.errors import AppError

ALLOWED = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


@dataclass
class ExtractedCv:
    extension: str
    mime_type: str
    text: str
    status: str
    error: str | None = None


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.replace("\x00", "")).strip()[:100_000]


def validate_and_extract(filename: str, content: bytes, max_bytes: int) -> ExtractedCv:
    if not content:
        raise AppError(422, "empty_cv", "The CV file is empty.")
    if len(content) > max_bytes:
        raise AppError(413, "cv_too_large", f"CV files must be no larger than {max_bytes // (1024 * 1024)} MB.")
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED:
        raise AppError(415, "unsupported_cv_type", "Only PDF, DOCX, and TXT files are supported.")

    try:
        if extension == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise AppError(415, "invalid_pdf", "The file does not contain a valid PDF signature.")
            text = _clean("\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages))
        elif extension == ".docx":
            if not zipfile.is_zipfile(io.BytesIO(content)):
                raise AppError(415, "invalid_docx", "The file is not a valid DOCX document.")
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise AppError(415, "invalid_docx", "The DOCX document is missing its main document content.")
            document = Document(io.BytesIO(content))
            text = _clean("\n".join(paragraph.text for paragraph in document.paragraphs))
        else:
            if content.startswith((b"%PDF-", b"PK\x03\x04")):
                raise AppError(415, "mismatched_cv_type", "The file content does not match its .txt extension.")
            try:
                text = _clean(content.decode("utf-8-sig"))
            except UnicodeDecodeError as exc:
                raise AppError(415, "invalid_txt", "TXT files must use UTF-8 text encoding.") from exc
    except AppError:
        raise
    except Exception as exc:
        raise AppError(422, "cv_extraction_failed", "The CV could not be safely parsed.") from exc

    if not text:
        return ExtractedCv(extension, ALLOWED[extension], "", "failed", "No extractable text was found. Scanned PDFs are not supported yet.")
    return ExtractedCv(extension, ALLOWED[extension], text, "complete")


