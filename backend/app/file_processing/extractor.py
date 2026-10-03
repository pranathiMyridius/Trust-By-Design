from pathlib import Path
import csv
import io
import re
import zipfile

import olefile
from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {
    ".docx",
    ".doc",
    ".xlsx",
    ".pdf",
    ".txt",
    ".csv",
}


def expand_uploads(filename: str, file_content: bytes) -> list[tuple[str, bytes]]:
    """
    If `filename` is a .zip archive, returns one (filename, content) pair
    per supported document inside it (skipping folders, __MACOSX/hidden
    entries, and any member whose extension isn't in
    SUPPORTED_EXTENSIONS). Otherwise returns [(filename, file_content)]
    unchanged. Lets callers that accept one uploaded document also accept
    a zip of several, without duplicating archive-handling logic.
    """

    if Path(filename).suffix.lower() != ".zip":
        return [(filename, file_content)]

    expanded: list[tuple[str, bytes]] = []

    with zipfile.ZipFile(io.BytesIO(file_content)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            member_name = Path(info.filename).name

            if not member_name or member_name.startswith("."):
                continue

            if "__MACOSX" in info.filename:
                continue

            if Path(member_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            expanded.append((member_name, archive.read(info)))

    return expanded


def extract_text(filename: str, file_content: bytes) -> str:
    """
    Extract readable text from supported document types.

    Supported:
    - DOCX
    - DOC (legacy Word 97-2003)
    - XLSX
    - PDF
    - TXT
    - CSV
    """

    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {extension}"
        )

    if extension == ".docx":
        return _extract_docx(file_content)

    if extension == ".doc":
        return _extract_doc(file_content)

    if extension == ".xlsx":
        return _extract_xlsx(file_content)

    if extension == ".pdf":
        return _extract_pdf(file_content)

    if extension == ".txt":
        return _extract_txt(file_content)

    if extension == ".csv":
        return _extract_csv(file_content)

    raise ValueError(
        f"Unsupported file type: {extension}"
    )


def _extract_docx(file_content: bytes) -> str:
    """Extract paragraphs and table content from Word."""

    try:
        document = Document(
            io.BytesIO(file_content)
        )
    except (zipfile.BadZipFile, KeyError):
        # Some files are saved/renamed as .docx but are actually the
        # legacy binary Word 97-2003 format (or vice versa). Fall back
        # to the .doc extractor instead of failing outright.
        return _extract_doc(file_content)

    sections = []

    # Extract paragraphs
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()

        if text:
            sections.append(text)

    # Extract tables
    for table in document.tables:
        for row in table.rows:
            row_values = []

            for cell in row.cells:
                value = cell.text.strip()

                if value:
                    row_values.append(value)

            if row_values:
                sections.append(" | ".join(row_values))

    return "\n".join(sections)


def _extract_doc(file_content: bytes) -> str:
    """
    Best-effort text extraction from a legacy .doc (OLE / Word 97-2003)
    file, using only pure-Python dependencies (no MS Word, no
    LibreOffice, no system binaries required).

    Legacy .doc text is stored as a mix of UTF-16LE runs and single-byte
    runs inside the "WordDocument" stream, interleaved with binary
    formatting data. Fully parsing the FIB/CLX piece table is the only
    way to get a byte-perfect result; instead we scan the stream for
    printable text runs, which reliably recovers the readable body text
    that the downstream AI extraction step needs.
    """

    try:
        with olefile.OleFileIO(io.BytesIO(file_content)) as ole:
            if not ole.exists("WordDocument"):
                # Some files are saved/renamed as .doc but are actually
                # the modern .docx (zip) format. Fall back rather than
                # failing outright.
                return _extract_docx(file_content)

            stream = ole.openstream("WordDocument").read()
    except OSError:
        # Not a valid OLE compound file at all -- try .docx as a
        # last resort in case of a mismatched extension.
        return _extract_docx(file_content)

    # UTF-16LE runs (most text in modern-era .doc files is stored this way).
    utf16_runs = re.findall(
        rb"(?:[\x20-\x7e\xa0-\xff]\x00){4,}",
        stream,
    )
    utf16_text = [
        run.decode("utf-16-le", errors="ignore")
        for run in utf16_runs
    ]

    # Plain single-byte printable runs (fallback / older documents).
    ascii_runs = re.findall(
        rb"[\x20-\x7e]{4,}",
        stream,
    )
    ascii_text = [
        run.decode("latin-1", errors="ignore")
        for run in ascii_runs
    ]

    # Prefer UTF-16 runs when present (they tend to be the real body
    # text); fall back to single-byte runs otherwise.
    candidates = (
        utf16_text
        if len(" ".join(utf16_text)) > len(" ".join(ascii_text))
        else ascii_text
    )

    # Drop obvious noise: very short fragments and fragments that are
    # mostly non-alphanumeric (leftover binary/formatting artifacts).
    cleaned = []

    for fragment in candidates:
        fragment = fragment.strip()

        if len(fragment) < 3:
            continue

        alnum_ratio = (
            sum(c.isalnum() or c.isspace() for c in fragment)
            / len(fragment)
        )

        if alnum_ratio < 0.6:
            continue

        cleaned.append(fragment)

    return "\n".join(cleaned)


def _extract_xlsx(file_content: bytes) -> str:
    """Extract values from all Excel worksheets."""

    workbook = load_workbook(
        filename=io.BytesIO(file_content),
        read_only=True,
        data_only=True,
    )

    sections = []

    for worksheet in workbook.worksheets:

        sections.append(
            f"[Sheet: {worksheet.title}]"
        )

        for row in worksheet.iter_rows(
            values_only=True
        ):
            values = []

            for value in row:
                if value is not None:
                    values.append(str(value).strip())

            if values:
                sections.append(
                    " | ".join(values)
                )

    return "\n".join(sections)


def _extract_pdf(file_content: bytes) -> str:
    """Extract text from all PDF pages."""

    reader = PdfReader(
        io.BytesIO(file_content)
    )

    sections = []

    for page_number, page in enumerate(
        reader.pages,
        start=1,
    ):
        text = page.extract_text()

        if text:
            sections.append(
                f"[Page {page_number}]"
            )
            sections.append(
                text.strip()
            )

    return "\n".join(sections)


def _extract_txt(file_content: bytes) -> str:
    """Extract text from a plain text file."""

    return file_content.decode(
        "utf-8",
        errors="replace",
    ).strip()


def _extract_csv(file_content: bytes) -> str:
    """Extract CSV rows as readable text."""

    decoded = file_content.decode(
        "utf-8",
        errors="replace",
    )

    reader = csv.reader(
        io.StringIO(decoded)
    )

    sections = []

    for row in reader:
        values = [
            value.strip()
            for value in row
            if value.strip()
        ]

        if values:
            sections.append(
                " | ".join(values)
            )

    return "\n".join(sections)