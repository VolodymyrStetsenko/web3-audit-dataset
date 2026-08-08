import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


PROJECT_ROOT = Path(__file__).parents[1]


def load_json(relative_path: str) -> dict:
    return json.loads(PROJECT_ROOT.joinpath(relative_path).read_text())


def test_synthetic_canonical_document_matches_schema() -> None:
    schema = load_json("schemas/canonical-document.schema.json")
    document = load_json("examples/synthetic/canonical-document.json")

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)
    assert document["text"] == document["page_content"]


def test_synthetic_rag_chunk_matches_schema_and_digest() -> None:
    schema = load_json("schemas/rag-chunk.schema.json")
    chunk = load_json("examples/synthetic/rag-chunk.json")

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(chunk)
    assert chunk["text"] == chunk["page_content"]
    assert chunk["id"] == f"{chunk['metadata']['document_id']}:00000"
    assert chunk["metadata"]["content_sha256"] == hashlib.sha256(
        chunk["text"].encode()
    ).hexdigest()