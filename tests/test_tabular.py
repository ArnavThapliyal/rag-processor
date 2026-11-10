import json

import pytest
from openpyxl import Workbook

from pipeline.schema import CONTENT_KIND_TABLE, TableData
from pipeline.tabular import read_table_chunks, table_chunks


def read(path, kind, **kw):
    return list(read_table_chunks(path, kind, **kw))


def test_csv_header_repeats_in_every_chunk_and_row_ranges_are_contiguous(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("sym,price\n" + "\n".join(f"S{i},{i}" for i in range(1, 11)) + "\n")
    chunks = read(p, "csv", rows_per_chunk=4)
    assert [(c.metadata["row_start"], c.metadata["row_end"]) for c in chunks] == [(1, 4), (5, 8), (9, 10)]
    assert [c.index for c in chunks] == [0, 1, 2]
    for c in chunks:
        assert c.content_kind == CONTENT_KIND_TABLE
        assert c.text.splitlines()[0] == "| sym | price |"
        assert c.metadata["columns"] == ["sym", "price"]


def test_csv_preserves_numeric_strings_and_fixes_headers(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("id,,id\n0010,1.50,x\n,,\n0020,2,y\n")  # blank + duplicate header, a fully blank row
    (chunk,) = read(p, "csv")
    assert chunk.metadata["columns"] == ["id", "col_2", "id_2"]
    assert "0010" in chunk.text and "1.50" in chunk.text  # not coerced to 10 / 1.5
    assert chunk.metadata["row_end"] == 2  # blank row dropped


def test_csv_semicolon_delimiter_and_latin1(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes("name;value\nCafé;1\n".encode("cp1252"))
    (chunk,) = read(p, "csv")
    assert chunk.metadata["columns"] == ["name", "value"] and "Café" in chunk.text


def test_row_size_budget_splits_chunks(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("a,b\n" + "\n".join(f"{'x' * 50},{i}" for i in range(20)) + "\n")
    chunks = read(p, "csv", rows_per_chunk=1000, max_size=200)
    assert len(chunks) > 1 and all(len(c.text) <= 260 for c in chunks)
    assert sum(c.metadata["row_end"] - c.metadata["row_start"] + 1 for c in chunks) == 20


def test_max_rows_truncates_with_warning(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("a\n" + "\n".join(str(i) for i in range(50)) + "\n")
    warnings = []
    chunks = read(p, "csv", max_rows=10, rows_per_chunk=100, warnings=warnings)
    assert chunks[-1].metadata["row_end"] == 10 and any("truncated" in w for w in warnings)


def test_xlsx_multi_sheet_numbers_chunks_consecutively(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Q1"
    ws.append(["name", "revenue"]); ws.append(["A", 100.0]); ws.append(["B", 2.5])
    ws2 = wb.create_sheet("Q2")
    ws2.append(["name", "revenue"]); ws2.append(["C", 7])
    p = tmp_path / "a.xlsx"
    wb.save(p)
    chunks = read(p, "xlsx")
    assert [c.metadata["sheet"] for c in chunks] == ["Q1", "Q2"]
    assert [c.index for c in chunks] == [0, 1]
    assert "| A | 100 |" in chunks[0].text and "2.5" in chunks[0].text  # 100.0 -> 100, 2.5 kept


def test_json_list_nested_object_and_jsonl(tmp_path):
    records = [{"id": 1, "meta": {"k": "v"}, "tags": ["a", "b"]}, {"id": 2, "extra": True}]
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"data": records, "page": 1}))
    (chunk,) = read(p, "json")
    assert chunk.metadata["columns"] == ["id", "meta.k", "tags", "extra"]
    assert '["a", "b"]' in chunk.text

    q = tmp_path / "b.jsonl"
    q.write_text("\n".join(json.dumps(r) for r in records))
    assert read(q, "json")[0].metadata["columns"] == ["id", "meta.k", "tags", "extra"]


def test_json_without_tabular_shape_raises(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError):
        read(p, "json")


def test_unknown_source_type_raises(tmp_path):
    with pytest.raises(ValueError):
        read(tmp_path / "x", "pdf")


def test_table_chunks_for_in_memory_tables_carry_provenance():
    t = TableData(header=["a", "b"], rows=[[str(i), "v"] for i in range(5)])
    chunks = table_chunks(t, start_index=7, rows_per_chunk=2, section_path="Sec", page_range="3")
    assert [c.index for c in chunks] == [7, 8, 9]
    assert all(c.section_path == "Sec" and c.page_range == "3" for c in chunks)
