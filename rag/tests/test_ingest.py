"""
Tests for the RAG ingestion pipeline and CLI formatting.

Covers: metadata parsing, document building, section splitting,
resumability filtering, and CLI source formatting.

Run from project root:
    python -m pytest rag/tests/ -v
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from rag.ingest import (
    _filter_new_documents,
    _parse_int,
    build_documents,
    parse_youtube_md,
)
from rag.cli import _format_sources


# ---------------------------------------------------------------------------
# Fixtures — sample markdown content
# ---------------------------------------------------------------------------

SAMPLE_MD_FULL = dedent("""\
    # Nasir Core vs Rob Colon

    **Match date:** 2026-06-27

    **YouTube:** https://www.youtube.com/watch?v=QKhXgjdzvac


    ## Video Metadata

    - **Title:** Rob Colon SHOCKS Nasir Core In An INSTANT CLASSIC | 31-28
    - **Channel:** Ballislife
    - **Views:** 140,881
    - **Likes:** 4,900
    - **Published:** 2026-06-27T23:00:10Z
    - **Duration:** 30m 30s


    ## Transcript

    "I don't care who they put in front of me," said Nas. "I'm built for this."
    Rob with a clutch three to take the lead. 31 to 28 final. Rob Colon wins.


    ## Top Comments (20)

    - **@freezea0** (661 likes): Rob really showed up when it mattered most
    - **@Desjrx2** (442 likes): Yo these refs just there for decoration
""")

SAMPLE_MD_NO_TRANSCRIPT = dedent("""\
    # Nasir Core vs Beno

    **Match date:** 2025-10-21

    **YouTube:** https://www.youtube.com/watch?v=NMwKJMpsI5U


    ## Video Metadata

    - **Title:** NBA G League Pro CALLS OUT Nasir Core
    - **Channel:** Ballislife
    - **Views:** 578,542
    - **Likes:** 16,738
    - **Duration:** 30m 47s


    ## Top Comments (20)

    - **@jimparkgm** (1506 likes): Watching ego get destroyed like this is quite an experience
    - **@freddee2189** (3520 likes): "He had a good weekend he should've left me alone"
""")

SAMPLE_MD_MINIMAL = dedent("""\
    # Player A vs Player B

    **Match date:** 2025-01-01
""")


@pytest.fixture
def md_full(tmp_path: Path) -> Path:
    p = tmp_path / "yt_nasir-core-vs-rob-colon-6-27-2026.md"
    p.write_text(SAMPLE_MD_FULL, encoding="utf-8")
    return p


@pytest.fixture
def md_no_transcript(tmp_path: Path) -> Path:
    p = tmp_path / "yt_nasir-core-vs-beno-10-21-2025.md"
    p.write_text(SAMPLE_MD_NO_TRANSCRIPT, encoding="utf-8")
    return p


@pytest.fixture
def md_minimal(tmp_path: Path) -> Path:
    p = tmp_path / "yt_minimal-match-1-1-2025.md"
    p.write_text(SAMPLE_MD_MINIMAL, encoding="utf-8")
    return p


@pytest.fixture
def md_dir_mixed(tmp_path: Path) -> Path:
    """Directory with three markdown files: full, no transcript, minimal."""
    (tmp_path / "yt_full.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
    (tmp_path / "yt_no_transcript.md").write_text(SAMPLE_MD_NO_TRANSCRIPT, encoding="utf-8")
    (tmp_path / "yt_minimal.md").write_text(SAMPLE_MD_MINIMAL, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# parse_youtube_md
# ---------------------------------------------------------------------------


class TestParseYoutubeMd:
    def test_extracts_player1(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["player1"] == "Nasir Core"

    def test_extracts_player2(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["player2"] == "Rob Colon"

    def test_extracts_match_date(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["match_date"] == "2026-06-27"

    def test_extracts_youtube_url(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["youtube_url"] == "https://www.youtube.com/watch?v=QKhXgjdzvac"

    def test_extracts_title(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert "Rob Colon SHOCKS" in result["metadata"]["title"]

    def test_extracts_channel(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["channel"] == "Ballislife"

    def test_extracts_views_as_int(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["views"] == 140881
        assert isinstance(result["metadata"]["views"], int)

    def test_extracts_likes_as_int(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["likes"] == 4900

    def test_extracts_duration(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["duration"] == "30m 30s"

    def test_extracts_source_file(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert result["metadata"]["source_file"] == md_full.name

    def test_extracts_transcript_section(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert "said Nas" in result["transcript"]
        assert "31 to 28" in result["transcript"]

    def test_extracts_comments_section(self, md_full: Path):
        result = parse_youtube_md(md_full)
        assert "@freezea0" in result["comments"]
        assert "showed up when it mattered" in result["comments"]

    def test_no_transcript_returns_empty(self, md_no_transcript: Path):
        result = parse_youtube_md(md_no_transcript)
        assert result["transcript"] == ""

    def test_comments_without_transcript(self, md_no_transcript: Path):
        result = parse_youtube_md(md_no_transcript)
        assert "@jimparkgm" in result["comments"]

    def test_minimal_file_no_crash(self, md_minimal: Path):
        result = parse_youtube_md(md_minimal)
        assert result["metadata"]["player1"] == "Player A"
        assert result["metadata"]["player2"] == "Player B"
        assert result["transcript"] == ""
        assert result["comments"] == ""

    def test_views_with_commas(self, md_no_transcript: Path):
        result = parse_youtube_md(md_no_transcript)
        assert result["metadata"]["views"] == 578542


# ---------------------------------------------------------------------------
# _parse_int
# ---------------------------------------------------------------------------


class TestParseInt:
    def test_plain_number(self):
        assert _parse_int("42") == 42

    def test_number_with_commas(self):
        assert _parse_int("1,234,567") == 1234567

    def test_zero(self):
        assert _parse_int("0") == 0


# ---------------------------------------------------------------------------
# build_documents
# ---------------------------------------------------------------------------


class TestBuildDocuments:
    def test_creates_transcript_and_comment_docs(self, md_dir_mixed: Path):
        docs = build_documents(md_dir_mixed)
        sections = [d.metadata["section"] for d in docs]
        assert "transcript" in sections
        assert "comments" in sections

    def test_file_with_transcript_creates_two_docs(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
        docs = build_documents(tmp_path)
        assert len(docs) == 2
        assert {d.metadata["section"] for d in docs} == {"transcript", "comments"}

    def test_file_without_transcript_creates_one_doc(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_NO_TRANSCRIPT, encoding="utf-8")
        docs = build_documents(tmp_path)
        assert len(docs) == 1
        assert docs[0].metadata["section"] == "comments"

    def test_minimal_file_creates_no_docs(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_MINIMAL, encoding="utf-8")
        docs = build_documents(tmp_path)
        assert len(docs) == 0

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        docs = build_documents(tmp_path)
        assert docs == []

    def test_ignores_non_yt_files(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("# Not a match", encoding="utf-8")
        (tmp_path / "yt_match.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
        docs = build_documents(tmp_path)
        source_files = [d.metadata["source_file"] for d in docs]
        assert all(f.startswith("yt_") for f in source_files)

    def test_metadata_propagated_to_documents(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
        docs = build_documents(tmp_path)
        for doc in docs:
            assert "player1" in doc.metadata
            assert "youtube_url" in doc.metadata
            assert doc.metadata["channel"] == "Ballislife"

    def test_excluded_metadata_keys_set(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
        docs = build_documents(tmp_path)
        for doc in docs:
            assert "source_file" in doc.excluded_llm_metadata_keys
            assert "section" in doc.excluded_llm_metadata_keys
            assert "source_file" in doc.excluded_embed_metadata_keys

    def test_document_count_across_mixed_dir(self, md_dir_mixed: Path):
        docs = build_documents(md_dir_mixed)
        # full: 2 docs (transcript + comments)
        # no_transcript: 1 doc (comments only)
        # minimal: 0 docs
        assert len(docs) == 3


# ---------------------------------------------------------------------------
# _filter_new_documents
# ---------------------------------------------------------------------------


class TestFilterNewDocuments:
    def test_filters_already_ingested(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
        docs = build_documents(tmp_path)
        already = {"yt_test.md"}
        filtered = _filter_new_documents(docs, already)
        assert len(filtered) == 0

    def test_keeps_new_documents(self, tmp_path: Path):
        (tmp_path / "yt_test.md").write_text(SAMPLE_MD_FULL, encoding="utf-8")
        docs = build_documents(tmp_path)
        filtered = _filter_new_documents(docs, set())
        assert len(filtered) == len(docs)

    def test_partial_filter(self, md_dir_mixed: Path):
        docs = build_documents(md_dir_mixed)
        already = {"yt_full.md"}  # only this one is ingested
        filtered = _filter_new_documents(docs, already)
        remaining_files = {d.metadata["source_file"] for d in filtered}
        assert "yt_full.md" not in remaining_files
        assert "yt_no_transcript.md" in remaining_files


# ---------------------------------------------------------------------------
# _format_sources (CLI)
# ---------------------------------------------------------------------------


class TestFormatSources:
    def test_no_source_nodes_returns_empty(self):
        response = MagicMock(spec=[])  # no source_nodes attribute
        assert _format_sources(response) == ""

    def test_empty_source_nodes_returns_empty(self):
        response = MagicMock()
        response.source_nodes = []
        assert _format_sources(response) == ""

    def test_formats_single_source(self):
        node = MagicMock()
        node.metadata = {
            "source_file": "yt_test.md",
            "player1": "Nasir Core",
            "player2": "Rob Colon",
            "youtube_url": "https://youtube.com/watch?v=abc",
            "section": "transcript",
        }
        node.score = 0.95
        node.get_content.return_value = "Some transcript text here"

        response = MagicMock()
        response.source_nodes = [node]

        result = _format_sources(response)
        assert "yt_test.md" in result
        assert "Nasir Core vs Rob Colon" in result
        assert "youtube.com" in result
        assert "transcript" in result
        assert "0.950" in result

    def test_deduplicates_by_source_file(self):
        node1 = MagicMock()
        node1.metadata = {"source_file": "yt_same.md"}
        node1.score = 0.9
        node1.get_content.return_value = "chunk 1"

        node2 = MagicMock()
        node2.metadata = {"source_file": "yt_same.md"}
        node2.score = 0.8
        node2.get_content.return_value = "chunk 2"

        response = MagicMock()
        response.source_nodes = [node1, node2]

        result = _format_sources(response)
        assert result.count("yt_same.md") == 1
