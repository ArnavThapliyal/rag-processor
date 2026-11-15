# 2. Ingestion workflow

## Command

```bash
uv run python main.py [ROOT]
```

`ROOT` is a directory or a single file. It defaults to `INPUT_DIR` (`data/input`). If ROOT does not exist, the command prints an error and exits with code 1 before creating anything.

Each invocation is one batch run. The run prints a one-line summary and a list of failed files:

```
3 file(s) found under /data/vendor_drop: 1 indexed, 0 failed, 2 skipped | chunks written: 42
```

| Exit code | Meaning |
|---|---|
| 0 | No file failed (skipped files do not count as failures) |
| 1 | At least one file failed, ROOT is missing, or the run was interrupted |

## Discovery (`pipeline.pipeline.discover_files`)

ROOT is walked with `Path.rglob("*")` and the result is sorted, so the run order is deterministic.

A path is **not** ingested when it is:

- a hidden file, or inside a hidden directory (any path part starting with `.`);
- a symlink (a linked file would otherwise be ingested twice);
- inside the pipeline's working directories (`SCRATCH_DIR`, `LOG_DIR`, `LANCEDB_PATH`), which matters when ROOT is the data directory itself;
- a metadata file: `*.meta.json`, `metadata.json`, `manifest.json`.

Folder names and nesting depth are ignored. Identical files at two different paths are currently both ingested; document-level deduplication is planned.

## Routing (`pipeline.type_router`)

`open_routed(path, scratch_dir)` is a context manager that yields a list of `RoutedFile` objects, one per processable unit.

| Detected type | Handler | Processed by |
|---|---|---|
| `pdf`, `docx`, `pptx`, `html` | `structured_doc_handler` | `parser.py` (docling) |
| `csv`, `xlsx` (incl. `.xlsm`), `xls`, `json` (incl. `.jsonl`, `.ndjson`) | `tabular_handler` | `tabular.py` |
| `doc`, `ppt`, anything unrecognised | `unsupported` | Counted as skipped, logged with a reason |

Detection rules:

- The type comes from the file's content (libmagic) combined with its extension. When they disagree, content wins.
- Ambiguous content types (`text/plain`, `application/octet-stream`) fall back to the extension.
- Office files sometimes sniff as plain zip; in that case a `.docx`/`.pptx`/`.xlsx` extension is trusted.

Zip archives:

- Members are extracted into a temporary directory under `SCRATCH_DIR` that exists until the `with` block exits, then is removed.
- Each member is routed like a normal file and recorded as `archive.zip!member.pdf`.
- Path-traversal entries, `__MACOSX` and `.DS_Store` are skipped.
- Limits: `ZIP_MAX_MEMBERS`, `ZIP_MAX_UNCOMPRESSED_BYTES`, `ZIP_MAX_DEPTH` (nested zips).

## Metadata (`pipeline.metadata`)

`build_source_document(routed, resolver)` returns a `SourceDocument`. It never raises because of metadata: a missing, empty or malformed source produces null fields and a logged warning.

Lookup order, where earlier sources win and later ones only fill gaps:

1. **Sidecar**: `<filename>.meta.json` beside the file (for a zip member, the sidecar of the archive).
2. **Batch file, per-file entry**: `metadata.json` → `files.<filename>`.
3. **Batch file, defaults**: `metadata.json` → `defaults`.
4. **Legacy `manifest.json`**: supplies `source_url` only.

Batch files are searched from the file's directory up to ROOT; the nearest directory wins. Files above ROOT are ignored.

```json
{
  "defaults": {"vendor": "acme", "sector": "IT"},
  "files": {
    "q2_results.pdf": {"ticker": "TCS", "doc_type": "quarterly_results", "roles": ["analyst"]}
  }
}
```

Field mapping (case-insensitive):

| Column | Accepted vendor keys |
|---|---|
| `symbol` | `symbol`, `ticker`, `scrip`, `scrip_code` |
| `source_url` | `source_url`, `url`, `link`, `source` |
| `doc_type` | `doc_type`, `document_type`, `type`, `category` |
| `vendor` | `vendor`, `provider`, `supplier`, `publisher` |
| `published_at` | `published_at`, `published`, `publication_date`, `date`, `published_date` |
| `allowed_roles` | `allowed_roles`, `roles`, `entitlements`, `access_roles` |
| any financial column | the column's exact name, e.g. `isin`, `close`, `roe_pct` |

Everything else, including the vendor's own document id, is stored in `metadata_json`. A financial value that cannot be converted to its column type becomes null and the original value is kept in `metadata_json`.

## Failure model

- Failures are isolated per file. An exception while routing or processing one file is logged with a traceback and the run continues.
- Unsupported files are **skipped**, not failed.
- The run summary lists `files_found`, `files_indexed`, `files_failed`, `files_skipped`, `chunks_written` and `failed_files`.

## Current limitations

These are properties of the half-migrated indexer, not of the design. All are covered in `NEXT_STEPS_PLAN.md`.

- Tabular files are routed correctly but then counted as skipped; they are not ingested yet.
- A file whose chunks are all already in the table is reported as **failed**, so re-running a batch exits with code 1.
- Rows are written by the legacy store into the old table, not the schema in [03_schema.md](03_schema.md).
- Runs are single-process.
