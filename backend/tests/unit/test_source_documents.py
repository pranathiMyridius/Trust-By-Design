"""
Source Library document processing: PDF validation, malware scanning,
extraction with page and section, chunking, hashing and failure handling.
No database and no HTTP.
"""

import pytest

from app.file_processing import malware_scan
from app.services import source_documents as docs
from app.services.source_documents import DocumentError
from tests.support.pdf import EICAR, encrypted, make_pdf

PAGE_ONE = "CHAPTER 1 CUSTOMER DUE DILIGENCE\n" + " ".join(["Regulated entities shall identify customers."] * 6)
PAGE_TWO = "2.1 Beneficial Ownership\n" + " ".join(["Identify the beneficial owner of every legal entity."] * 6)


# -- validation ---------------------------------------------------------------------


def test_a_valid_pdf_is_accepted_and_its_name_sanitised():
    name = docs.validate_pdf_upload("..\\..\\Policy.PDF", "application/pdf", make_pdf([PAGE_ONE]))
    assert name == "Policy.PDF"  # only the base name survives


@pytest.mark.parametrize(
    "filename, content_type, data, code",
    [
        ("policy.docx", "application/pdf", b"%PDF-1.4 x", "UNSUPPORTED_FILE_TYPE"),
        ("policy.pdf", "text/html", b"%PDF-1.4 x", "UNSUPPORTED_FILE_TYPE"),
        ("policy.pdf", "application/pdf", b"", "EMPTY_FILE"),
        ("policy.pdf", "application/pdf", b"MZ\x90\x00 not a pdf at all", "NOT_A_PDF"),
        ("", "application/pdf", b"%PDF-1.4", "UNSUPPORTED_FILE_TYPE"),
    ],
)
def test_invalid_uploads_are_refused_with_a_stable_code(filename, content_type, data, code):
    with pytest.raises(DocumentError) as raised:
        docs.validate_pdf_upload(filename, content_type, data)
    assert raised.value.code == code


def test_files_over_the_size_limit_are_refused(monkeypatch):
    monkeypatch.setattr(docs, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(DocumentError) as raised:
        docs.validate_pdf_upload("big.pdf", "application/pdf", b"%PDF-" + b"0" * 200)
    assert raised.value.code == "FILE_TOO_LARGE" and raised.value.status_code == 413


# -- malware scanning -----------------------------------------------------------------


def test_the_eicar_test_signature_is_flagged_infected():
    result = malware_scan.scan_bytes(make_pdf([PAGE_ONE], extra=EICAR))
    assert result.status == malware_scan.INFECTED and "EICAR" in result.detail


def test_pdf_javascript_is_refused_unless_explicitly_allowed(monkeypatch):
    pdf = make_pdf([PAGE_ONE], active_content=True)
    assert malware_scan.scan_bytes(pdf).status == malware_scan.INFECTED
    monkeypatch.setenv("SOURCE_PDF_ALLOW_ACTIVE_CONTENT", "true")
    assert malware_scan.scan_bytes(pdf).status == malware_scan.CLEAN


def test_scanning_fails_closed_when_an_engine_is_required_but_absent(monkeypatch):
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    monkeypatch.setenv("MALWARE_SCAN_REQUIRED", "true")
    result = malware_scan.scan_bytes(make_pdf([PAGE_ONE]))
    assert result.status == malware_scan.ERROR and "required" in result.detail


def test_a_clean_pdf_passes_the_heuristics_when_scanning_is_optional(monkeypatch):
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    monkeypatch.setenv("MALWARE_SCAN_REQUIRED", "false")
    assert malware_scan.scan_bytes(make_pdf([PAGE_ONE])).clean


def test_clamav_verdicts_are_honoured_and_an_unreachable_engine_is_an_error(monkeypatch):
    monkeypatch.setenv("CLAMAV_HOST", "clamav.invalid")
    replies = iter([malware_scan.ScanResult(malware_scan.INFECTED, "ClamAV: Win.Test detected")])
    monkeypatch.setattr(malware_scan, "_clamav_scan", lambda data: next(replies))
    assert malware_scan.scan_bytes(make_pdf([PAGE_ONE])).status == malware_scan.INFECTED

    monkeypatch.undo()
    monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMAV_PORT", "1")  # nothing listens here
    monkeypatch.setenv("CLAMAV_TIMEOUT_SECONDS", "1")
    result = malware_scan.scan_bytes(make_pdf([PAGE_ONE]))
    assert result.status == malware_scan.ERROR  # never "clean" when the scanner could not run


# -- extraction ---------------------------------------------------------------------------


def test_text_is_extracted_page_by_page():
    pages = docs.extract_pdf_pages(make_pdf([PAGE_ONE, PAGE_TWO]))
    assert len(pages) == 2
    assert "CUSTOMER DUE DILIGENCE" in pages[0] and "Beneficial Ownership" in pages[1]


def test_an_encrypted_pdf_is_refused_with_a_clear_reason():
    with pytest.raises(DocumentError) as raised:
        docs.extract_pdf_pages(encrypted(make_pdf([PAGE_ONE])))
    assert raised.value.code == "PDF_ENCRYPTED"


def test_a_pdf_without_a_text_layer_is_refused():
    with pytest.raises(DocumentError) as raised:
        docs.extract_pdf_pages(make_pdf(["", ""]))
    assert raised.value.code == "NO_TEXT_LAYER"


def test_a_corrupt_pdf_is_refused_not_crashed():
    with pytest.raises(DocumentError) as raised:
        docs.extract_pdf_pages(b"%PDF-1.4\nthis is not a real pdf body\n%%EOF")
    assert raised.value.code in {"PDF_UNREADABLE", "NO_TEXT_LAYER", "PDF_EMPTY"}


def test_nul_bytes_are_removed_because_postgres_rejects_them():
    assert docs.clean_text("a\x00b") == "ab"


# -- chunks keep page and section ------------------------------------------------------------


def test_chunks_carry_their_page_and_the_section_in_force():
    chunks = docs.build_chunks([PAGE_ONE, PAGE_TWO])
    assert chunks[0].page_start == 1 and chunks[0].section == "CHAPTER 1 CUSTOMER DUE DILIGENCE"
    last = chunks[-1]
    assert last.page_end == 2
    assert any(chunk.section == "2.1 Beneficial Ownership" for chunk in chunks)


def test_a_chunk_spanning_a_page_break_reports_both_pages():
    long_page_one = " ".join(f"alpha{n}" for n in range(200))
    chunks = docs.build_chunks([long_page_one, " ".join(f"beta{n}" for n in range(200))])
    spanning = [c for c in chunks if c.page_start == 1 and c.page_end == 2]
    assert spanning, [(c.page_start, c.page_end) for c in chunks]


def test_chunking_overlaps_so_a_sentence_on_a_boundary_stays_findable():
    words = " ".join(f"w{n}" for n in range(500))
    chunks = docs.build_chunks([words])
    assert len(chunks) >= 2
    first_tail = chunks[0].text.split()[-docs.CHUNK_OVERLAP_WORDS:]
    assert chunks[1].text.split()[: docs.CHUNK_OVERLAP_WORDS] == first_tail


def test_plain_text_chunks_have_no_page():
    chunks = docs.build_plain_chunks("SECTION 4: Screening\n" + "word " * 50)
    assert chunks and chunks[0].page_start is None and chunks[0].section.startswith("SECTION 4")


def test_the_document_hash_is_a_stable_sha256():
    data = make_pdf([PAGE_ONE])
    assert docs.sha256_hex(data) == docs.sha256_hex(bytes(data)) and len(docs.sha256_hex(data)) == 64
    assert docs.sha256_hex(data) != docs.sha256_hex(make_pdf([PAGE_TWO]))
