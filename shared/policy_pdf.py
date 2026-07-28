"""Structure-aware chunking for regulatory PDFs in policies/docs/.

Unlike the internal markdown policies (short, already-atomic — see
`policy_store._chunk_text`), these are long-form regulatory texts where a
uniform strategy doesn't fit: SFDR and the CONSOB regulation are organized
by numbered Article, while the two ESMA guideline documents number
paragraphs sequentially under topic headers instead. Each PDF is routed to
the parser matching its real structure so chunk boundaries line up with how
the regulator itself organizes the text — this keeps citations precise
(e.g. "SFDR, Article 8") and avoids splitting a provision mid-clause.

Extraction uses PyMuPDF (`fitz`), not pypdf: pypdf's text extraction
introduces spurious spaces inside words on some of these PDFs (e.g.
"Ar ticle" instead of "Article"), which breaks both marker regexes and
embedding quality. PyMuPDF does not have this problem on any of the four
documents this module was built against.
"""
import re
from pathlib import Path
from typing import Any

import fitz

_MIN_CHUNK_CHARS = 40


def _load_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _chunk_by_article(
    text: str, marker_pattern: str, doc_type: str, source: str,
) -> list[dict[str, Any]]:
    """Splits on an "Article N" / "Art. N" marker, one chunk per article.

    Regulatory PDFs typically list every article twice — once in the table
    of contents, once as the real body — and the ToC entry is short (just a
    title + page number) while the body is long. When an article number
    repeats, keep only its last occurrence (the body); this also
    self-corrects for ToC page-number noise without needing to locate where
    the ToC ends.
    """
    matches = list(re.finditer(marker_pattern, text, re.MULTILINE))
    if not matches:
        return []

    last_by_number: dict[str, int] = {}
    for i, m in enumerate(matches):
        # Rare PDF text-extraction artifact: a missing space merges an
        # article number with an adjacent page number (e.g. "Art. 146" +
        # page "81" -> "Art. 14681"). No regulatory document referenced
        # here numbers articles above 999 numerically, so this is a safe
        # ceiling for discarding the artifact rather than a real article.
        digits = re.match(r"\d+", m.group(1))
        if digits and int(digits.group()) > 999:
            continue
        last_by_number[m.group(1)] = i
    body_indices = sorted(last_by_number.values())

    chunks = []
    for pos, i in enumerate(body_indices):
        start = matches[i].start()
        end = matches[body_indices[pos + 1]].start() if pos + 1 < len(body_indices) else len(text)
        content = re.sub(r"\n{2,}", "\n", text[start:end]).strip()
        if len(content) < _MIN_CHUNK_CHARS:
            continue
        chunks.append({
            "content": content,
            "source": source,
            "metadata": {"doc_type": doc_type, "article": matches[i].group(1)},
        })
    return chunks


def _chunk_sfdr(path: Path) -> list[dict[str, Any]]:
    text = _load_text(path)
    # SFDR has no table of contents (unlike the CONSOB regulation), but
    # "Article N" appears constantly as an inline cross-reference (e.g.
    # "in accordance with Article 15 of Regulation..."). Those inline
    # citations are followed by a lowercase continuation ("of", "shall") or
    # "(1)"; a real article heading is alone on its line, followed by a
    # Title-Case heading on the next — this tells the two apart reliably.
    return _chunk_by_article(text, r"^Article (\d+)[ \t]*\n(?=[A-Z])", "SFDR", path.name)


def _chunk_consob(path: Path) -> list[dict[str, Any]]:
    text = _load_text(path)
    return _chunk_by_article(text, r"^Art\.\s*(\d+(?:-\w+)?)\b", "CONSOB", path.name)


def _chunk_by_topic_headers(
    path: Path, doc_type: str, header_font: str, header_size_range: tuple[float, float],
    start_marker: str | None = None, end_marker: str | None = None,
) -> list[dict[str, Any]]:
    """Splits on topic-header lines identified by font (bold spans at body
    text size), grouping the numbered paragraphs under each header into one
    chunk. Used for the two ESMA guideline documents, which number
    paragraphs sequentially across a whole section instead of resetting per
    article — a paragraph alone ("14. The potential target market...") is
    meaningless without the header above it, so the header is the real
    semantic boundary, not the paragraph number.
    """
    doc = fitz.open(path)
    try:
        lines: list[tuple[str, bool]] = []  # (text, is_header)
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = line["spans"]
                    if not spans:
                        continue
                    txt = "".join(s["text"] for s in spans).strip()
                    if not txt:
                        continue
                    lo, hi = header_size_range
                    is_header = all(
                        s["font"] == header_font and lo <= s["size"] <= hi for s in spans
                    )
                    lines.append((txt, is_header))
    finally:
        doc.close()

    full_text = "\n".join(t for t, _ in lines)
    start = full_text.find(start_marker) if start_marker else 0
    end = full_text.find(end_marker, start + 1) if end_marker else len(full_text)
    if start < 0:
        start = 0
    if end < 0:
        end = len(full_text)

    # Re-walk lines, only keeping those inside [start, end) of full_text.
    offset = 0
    kept: list[tuple[str, bool]] = []
    for txt, is_header in lines:
        line_start = offset
        offset += len(txt) + 1
        if line_start < start or line_start >= end:
            continue
        kept.append((txt, is_header))

    chunks: list[dict[str, Any]] = []
    current_header: str | None = None
    current_lines: list[str] = []

    def _flush():
        if current_header and current_lines:
            content = re.sub(r"\n{2,}", "\n", "\n".join(current_lines)).strip()
            if len(content) >= _MIN_CHUNK_CHARS:
                chunks.append({
                    "content": f"{current_header}\n{content}",
                    "source": path.name,
                    "metadata": {"doc_type": doc_type, "topic": current_header},
                })

    # Consecutive header lines (headers wrapping across two lines, as seen in
    # this document) are merged into a single header before the first
    # non-header line resets the group.
    pending_header_parts: list[str] = []
    for txt, is_header in kept:
        if is_header:
            pending_header_parts.append(txt)
            continue
        if pending_header_parts:
            _flush()
            current_header = " ".join(pending_header_parts)
            current_lines = []
            pending_header_parts = []
        current_lines.append(txt)
    _flush()

    return chunks


def _chunk_product_governance(path: Path) -> list[dict[str, Any]]:
    return _chunk_by_topic_headers(
        path, doc_type="ESMA_PG", header_font="Arial-BoldMT", header_size_range=(10.0, 11.5),
        start_marker="5.1 General", end_marker=None,
    )


def _chunk_suitability(path: Path) -> list[dict[str, Any]]:
    """Only Annex IV (the actual guideline text) is ingested — the
    preceding Executive Summary and consultation feedback are procedural
    history, not compliance rules a Compliance Agent should cite."""
    text = _load_text(path)
    start = text.find("3.4 Annex IV - Guidelines")
    # skip ToC entry, use body heading
    body_start = text.find("3.4 Annex IV - Guidelines", start + 1)
    if body_start < 0:
        body_start = start
    end = text.find("3.5 Annex V", body_start)
    if end < 0:
        end = len(text)
    annex = text[body_start:end]

    matches = list(re.finditer(r"General guideline (\d+)", annex))
    chunks = []
    for i, m in enumerate(matches):
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(annex)
        # Include the topic header + "Relevant legislation" line preceding
        # the marker (already emitted as part of the previous segment's
        # tail in the raw text), so pull from a bit before the marker.
        seg_start = m.start()
        content = re.sub(r"\n{2,}", "\n", annex[seg_start:seg_end]).strip()
        if len(content) < _MIN_CHUNK_CHARS:
            continue
        chunks.append({
            "content": content,
            "source": path.name,
            "metadata": {"doc_type": "ESMA_SUIT", "guideline_number": m.group(1)},
        })
    return chunks


_PARSERS = {
    "CELEX_32019R2088_EN_TXT.pdf": _chunk_sfdr,
    "reg_consob_2018_20307.pdf": _chunk_consob,
    "ESMA35-43-3448_Guidelines_on_product_governance.pdf": _chunk_product_governance,
    "esma35-43-3172_final_report_on_mifid_ii_guidelines_on_suitability.pdf": _chunk_suitability,
}


def chunk_pdf(path: Path) -> list[dict[str, Any]]:
    """Returns [{"content", "source", "metadata"}, ...] for a known
    regulatory PDF in policies/docs/. Raises KeyError for an unrecognized
    filename — a new PDF needs a parser added to `_PARSERS` above, since a
    generic fallback would silently produce badly-shaped chunks."""
    parser = _PARSERS.get(path.name)
    if parser is None:
        raise KeyError(
            f"No chunking parser registered for {path.name!r} — add one to "
            f"shared/policy_pdf.py::_PARSERS."
        )
    return parser(path)
