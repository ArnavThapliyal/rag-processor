"""Recursive discovery and indexer routing on an irregular directory tree."""

import os
import zipfile

import pytest

import pipeline.indexer as indexer_mod
import pipeline.pipeline as pp
from pipeline.pipeline import discover_files

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def rel(root, files):
    return sorted(str(f.relative_to(root)) for f in files)


@pytest.fixture
def messy_tree(tmp_path):
    root = tmp_path / "drop"
    (root / "any" / "depth" / "of" / "folders").mkdir(parents=True)
    (root / ".hidden_dir").mkdir()
    (root / "scratch").mkdir()
    files = {
        "top.csv": "a,b\n1,2\n",
        "any/report.pdf": PDF,
        "any/depth/of/folders/deep.json": '[{"x": 1}]',
        "any/depth/of/folders/deep.json.meta.json": '{"vendor": "acme"}',   # sidecar: not a document
        "any/metadata.json": '{"defaults": {"sector": "Energy"}}',          # batch file: not a document
        ".hidden_dir/secret.csv": "a\n1\n",
        ".DS_Store": "junk",
        "any/._resource": "junk",
        "scratch/notes.csv": "a\n1\n",                                       # an ordinary folder called scratch IS input
    }
    for name, content in files.items():
        p = root / name
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    os.symlink(root / "top.csv", root / "link_to_top.csv")
    os.symlink(root, root / "any" / "loop")
    return root


def test_discovery_is_recursive_ignores_layout_and_skips_non_documents(messy_tree):
    found = rel(messy_tree, discover_files(messy_tree))
    assert found == ["any/depth/of/folders/deep.json", "any/report.pdf", "scratch/notes.csv", "top.csv"]


def test_discovery_accepts_a_single_file_and_skips_pipeline_work_dirs(messy_tree, monkeypatch, tmp_path):
    assert discover_files(messy_tree / "top.csv") == [messy_tree / "top.csv"]

    work = messy_tree / "any" / "depth"
    monkeypatch.setattr(pp, "_WORK_DIRS", (work,))
    assert not any("depth" in str(f) for f in discover_files(messy_tree))


def test_runner_summary_and_invalid_indexer_result(messy_tree, monkeypatch):
    class Stub:
        def index_files(self, files, root):
            assert root == messy_tree and len(files) == 4
            return {"status": "success", "files_indexed": 3, "files_failed": 1, "files_skipped": 0,
                    "chunks_written": 9, "failed_files": ["x.pdf"]}

    monkeypatch.setattr(pp, "Indexer", Stub)
    s = pp.PipelineRunner().run(messy_tree)
    assert (s["files_found"], s["files_indexed"], s["files_failed"], s["chunks_written"], s["failed_files"]) == (4, 3, 1, 9, ["x.pdf"])

    class Bad:
        def index_files(self, files, root):
            return None

    monkeypatch.setattr(pp, "Indexer", Bad)
    with pytest.raises(ValueError):
        pp.PipelineRunner().run(messy_tree)


def test_indexer_routes_by_content_not_folder_and_applies_nearest_metadata(messy_tree, tmp_path, monkeypatch):
    # a pdf-looking thing inside a folder named 'csv', a csv named .pdf, and a zip with a nested pdf
    (messy_tree / "csv").mkdir()
    (messy_tree / "csv" / "actually.pdf").write_bytes(PDF)
    (messy_tree / "any" / "bundle.zip").write_bytes(b"")
    with zipfile.ZipFile(messy_tree / "any" / "bundle.zip", "w") as z:
        z.writestr("inner/doc.pdf", PDF)
    monkeypatch.setattr(indexer_mod, "SCRATCH_DIR", tmp_path / "scratch_work")

    seen = []

    def fake_index_file(self, item, doc):
        assert item.path.exists()  # zip members must still be on disk while indexed
        seen.append((doc.source_filename, doc.source_type, doc.vendor, doc.fields.get("sector")))
        return 3

    monkeypatch.setattr(indexer_mod.Indexer, "_index_file", fake_index_file)
    result = indexer_mod.Indexer().index_files(discover_files(messy_tree), messy_tree)

    by_name = {n: (t, v, sec) for n, t, v, sec in seen}
    assert by_name["actually.pdf"][0] == "pdf"                       # folder named 'csv' is irrelevant
    assert by_name["bundle.zip!doc.pdf"] == ("pdf", None, "Energy")  # zip member, batch metadata from any/
    assert by_name["report.pdf"] == ("pdf", None, "Energy")
    assert result["files_indexed"] == 3 and result["chunks_written"] == 9
    assert result["files_skipped"] == 3   # top.csv, notes.csv, deep.json: routed as tabular, not yet indexed (Step 6)
    assert result["files_failed"] == 0 and result["status"] == "success"


def test_indexer_isolates_a_failing_file(tmp_path, monkeypatch):
    root = tmp_path / "r"
    root.mkdir()
    (root / "good.pdf").write_bytes(PDF)
    (root / "bad.pdf").write_bytes(PDF + b"x")
    monkeypatch.setattr(indexer_mod, "SCRATCH_DIR", tmp_path / "w")

    def fake(self, item, doc):
        if item.display_name == "bad.pdf":
            raise RuntimeError("boom")
        return 2

    monkeypatch.setattr(indexer_mod.Indexer, "_index_file", fake)
    result = indexer_mod.Indexer().index_files(discover_files(root), root)
    assert (result["files_indexed"], result["files_failed"], result["failed_files"]) == (1, 1, ["bad.pdf"])
