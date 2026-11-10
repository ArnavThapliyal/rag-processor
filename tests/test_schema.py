import math

import lancedb
import pyarrow as pa
import pytest

from pipeline.schema import (
    VECTOR_INDEX_KWARGS, Chunk, ChunkRecord, SourceDocument, TableData,
    build_record, compute_doc_id, make_chunk_id, normalize_allowed_roles,
)

KW = dict(ingested_at="2026-11-10T00:00:00+00:00", ingest_run_id="r1",
          embedding_model="BAAI/bge-m3", pipeline_version="0.1.0")


def unit_vec(*head):
    v = list(head) + [0.0] * (1024 - len(head))
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def doc(**over):
    base = dict(path="x", doc_id="a" * 32, source_type="pdf", source_filename="x.pdf")
    return SourceDocument(**{**base, **over})


def test_chunk_id_is_deterministic_and_sensitive():
    a = make_chunk_id("d" * 32, 0, "Disclaimer")
    assert a == make_chunk_id("d" * 32, 0, "Disclaimer") and len(a) == 32
    assert a != make_chunk_id("d" * 32, 1, "Disclaimer")  # repeated text in one doc must not collide
    assert a != make_chunk_id("d" * 32, 0, "Disclaimer.")
    assert a != make_chunk_id("e" * 32, 0, "Disclaimer")


def test_doc_id_is_content_addressed(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"same")
    (tmp_path / "b.bin").write_bytes(b"same")
    (tmp_path / "c.bin").write_bytes(b"diff")
    assert compute_doc_id(tmp_path / "a.bin") == compute_doc_id(tmp_path / "b.bin")
    assert compute_doc_id(tmp_path / "a.bin") != compute_doc_id(tmp_path / "c.bin")


def test_vector_column_is_fixed_size_1024_and_metric_is_cosine():
    t = ChunkRecord.to_arrow_schema().field("embedding").type
    assert pa.types.is_fixed_size_list(t) and t.list_size == 1024
    assert VECTOR_INDEX_KWARGS["metric"] == "cosine"


def test_build_record_rejects_contract_violations():
    with pytest.raises(ValueError):
        build_record(doc(), Chunk(index=0, text="x"), [0.1] * 10, **KW)
    with pytest.raises(ValueError):
        build_record(doc(), Chunk(index=0, text="   "), unit_vec(1), **KW)
    with pytest.raises(ValueError):
        build_record(doc(), Chunk(index=0, text="x", content_kind="img"), unit_vec(1), **KW)


def test_lancedb_roundtrip_with_and_without_vendor_metadata(tmp_path):
    table = lancedb.connect(str(tmp_path)).create_table("t", schema=ChunkRecord)
    full = doc(doc_id="b" * 32, source_type="csv", symbol="TCS", allowed_roles=normalize_allowed_roles("analyst, admin"),
               extra={"vendor_doc_id": "X-9"})
    table.add([
        build_record(doc(), Chunk(index=0, text="hello"), unit_vec(1, 0), **KW),  # no vendor metadata at all
        build_record(full, Chunk(index=0, text="a | b", content_kind="table", metadata={"sheet": "S1"}), unit_vec(0, 1), **KW),
    ])
    rows = {r["doc_id"]: r for r in table.to_arrow().to_pylist()}
    assert rows["a" * 32]["allowed_roles"] is None and rows["a" * 32]["symbol"] is None
    assert rows["b" * 32]["allowed_roles"] == ["analyst", "admin"]
    assert '"sheet": "S1"' in rows["b" * 32]["metadata_json"] and "X-9" in rows["b" * 32]["metadata_json"]

    nearest = table.search(unit_vec(1, 0)).metric("cosine").limit(1).to_list()[0]
    assert nearest["doc_id"] == "a" * 32 and abs(nearest["_distance"]) < 1e-5


def test_table_markdown_repeats_header_and_escapes_cells():
    t = TableData(header=["a", "b"], rows=[["1", "x|y"], ["2", "line1\nline2"], ["3"]])
    md = t.to_markdown()
    assert md.splitlines()[0] == "| a | b |" and md.splitlines()[1] == "| --- | --- |"
    assert "x\\|y" in md and "line1 line2" in md and "| 3 |  |" in md
    assert t.to_markdown(1, 2).splitlines()[0] == "| a | b |"  # a slice still carries the header
