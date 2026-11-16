"""Inspect the LanceDB table and run searches.

    python view_db.py                         # configuration and contents
    python view_db.py "what did management say about margins"
    python view_db.py "closing price" --symbol POLYCAB -k 5
"""
import argparse
import os
import sys

DB = "/Users/arnav/Projects/Software/test symbol data/lancedb"
os.environ.setdefault("LANCEDB_PATH", DB)   # must be set before the pipeline imports

from pipeline.vector_store import VectorStore  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("query", nargs="?")
parser.add_argument("--symbol")
parser.add_argument("-k", type=int, default=5)
args = parser.parse_args()

store = VectorStore().open_or_create()
table = store.table

if not args.query:
    print(f"Database : {store.path}")
    print(f"Table    : {store.table_name}")
    print(f"Rows     : {table.count_rows()}")

    print("\nColumns:")
    for field in table.schema:
        print(f"  {field.name:32} {field.type}")

    print("\nIndexes:")
    indexes = table.list_indices()
    for index in indexes:
        print(f"  {index.name}: {index.index_type} on {index.columns}")
    if not indexes:
        print("  none (searches are exact scans)")

    cols = ["doc_id", "symbol", "source_type", "content_kind", "source_filename"]
    df = table.search().select(cols).to_arrow().to_pandas()
    print(f"\nDocuments: {df['doc_id'].nunique()}")
    print("\nRows by symbol:\n", df["symbol"].fillna("(none)").value_counts().to_string())
    print("\nRows by file type:\n", df["source_type"].value_counts().to_string())
    print("\nRows by content kind:\n", df["content_kind"].value_counts().to_string())

    print("\nSample rows:")
    sample_cols = ["symbol", "source_filename", "content_kind", "page_range", "section_path", "text"]
    for row in table.search().select(sample_cols).limit(3).to_list():
        print(f"\n  [{row['symbol']}] {row['source_filename']}  ({row['content_kind']}, page {row['page_range']})")
        print(f"  section: {row['section_path']}")
        print(f"  {row['text'][:300]!r}")
    sys.exit(0)

from pipeline.embedder import Embedder  # noqa: E402  (loads bge-m3, takes a few seconds)

vector = Embedder().encode([args.query])[0]
search = table.search(vector).metric("cosine").limit(args.k)