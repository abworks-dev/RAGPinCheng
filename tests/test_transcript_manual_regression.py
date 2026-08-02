import json
import os
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import patch
import pytest

from src.chunk import _parse_transcript_turns, chunk_transcript
from src.ingest import ParsedDoc
from tests.transcription_fixture_helpers import FIXTURE_DIR, MEDIA_ID, load_bytes, load_json


def make_doc(name,title):
    path=FIXTURE_DIR/name
    return ParsedDoc(source_path=path,category="教学视频",doc_title=title,
        markdown_path=path,doc_type="transcript",media_id=MEDIA_ID)


def chunk_view(result):
    parents, children = result
    return {
        "parents": [{"text":x.text,"doc_title":x.doc_title,"category":x.category,"section_path":x.section_path,"doc_type":x.doc_type,"start_time":x.start_time,"media_id":x.media_id} for x in parents],
        "children": [{"parent_id_matches":x.parent_id==parents[0].parent_id,"text":x.text,"embed_text":x.embed_text,"doc_title":x.doc_title,"category":x.category,"section_path":x.section_path,"content_type":x.content_type,"doc_type":x.doc_type,"start_time":x.start_time,"media_id":x.media_id} for x in children],
    }


@pytest.mark.parametrize("name,golden", [
    ("manual-transcript.md","manual-turns-golden.json"),
    ("automatic-transcript.md","automatic-turns-golden.json"),
])
def test_real_parser_matches_golden(name,golden):
    text=load_bytes(name).decode("utf-8")
    assert _parse_transcript_turns(text)==[tuple(x) for x in load_json(golden)]


@pytest.mark.parametrize("name,title,golden", [
    ("manual-transcript.md","人工转录测试","manual-chunks-golden.json"),
    ("automatic-transcript.md","自动转录测试","automatic-chunks-golden.json"),
])
def test_real_chunker_matches_golden(name,title,golden):
    assert chunk_view(chunk_transcript(make_doc(name,title)))==load_json(golden)


def test_real_admin_markdown_validation_and_classification_when_dependencies_available():
    if os.environ.get("RAGPINCHENG_SKIP_ADMIN_IMPORT") == "1":
        pytest.skip("local environment lacks repository-declared admin dependencies")
    def unavailable_collaborator(*args, **kwargs):
        raise AssertionError("unrelated admin collaborator must not run during helper regression")

    indexing_pipeline = ModuleType("src.indexing_pipeline")
    indexing_pipeline.delete_document = unavailable_collaborator
    indexing_pipeline.list_indexed_documents = unavailable_collaborator
    conversation_runtime = ModuleType("api.conversation_runtime")
    conversation_runtime.sweep_once = unavailable_collaborator
    indexing = ModuleType("api.indexing")
    indexing.create_job = unavailable_collaborator
    indexing.enqueue = unavailable_collaborator
    with patch.dict(
        sys.modules,
        {
            "src.indexing_pipeline": indexing_pipeline,
            "api.conversation_runtime": conversation_runtime,
            "api.indexing": indexing,
        },
    ):
        from api import routes_admin as routes

    manual=load_bytes("manual-transcript.md")
    assert routes._validate_transcript_markdown(manual) is None
    assert routes._classify_doc_type("video.md","教学视频")=="transcript"
    assert routes._classify_doc_type("ordinary.md","公司标准")=="pdf"


def test_manual_bytes_are_independent_from_asr_contracts():
    text=load_bytes("manual-transcript.md")
    assert b"profile_id" not in text and b"provider_key" not in text
    assert text==FIXTURE_DIR.joinpath("manual-transcript.md").read_bytes()
