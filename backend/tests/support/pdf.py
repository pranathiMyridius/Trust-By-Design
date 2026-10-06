"""
Builds small, valid, text-based PDFs for the Source Library tests, with no
third-party dependency (pypdf only reads and encrypts them).
"""

from __future__ import annotations

import io


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str], *, active_content: bool = False, extra: bytes = b"") -> bytes:
    """A PDF with one page per string (newlines become separate lines).
    `active_content` adds a JavaScript open-action; `extra` is appended
    after the document body (e.g. an EICAR test string)."""

    objects: list[bytes] = []
    page_ids = [4 + 2 * index for index in range(len(pages))]
    catalog = b"<< /Type /Catalog /Pages 2 0 R"
    if active_content:
        catalog += b" /OpenAction << /S /JavaScript /JS (app.alert\\(1\\)) >>"
    catalog += b" >>"
    objects.append(catalog)
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # object 3
    for index, page in enumerate(pages):
        content_id = page_ids[index] + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        lines = page.split("\n")
        stream = "BT /F1 11 Tf 50 740 Td 14 TL\n" + "\n".join(f"({_escape(line)}) Tj T*" for line in lines) + "\nET"
        raw = stream.encode("latin-1", "replace")
        objects.append(b"<< /Length " + str(len(raw)).encode() + b" >>\nstream\n" + raw + b"\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    out.write(extra)
    return out.getvalue()


def encrypted(pdf: bytes, password: str = "secret") -> bytes:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# The standard antivirus test string, assembled so this file is not itself flagged.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
