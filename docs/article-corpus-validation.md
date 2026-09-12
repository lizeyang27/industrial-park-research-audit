# Article corpus validation

The corpus validator accepts either one UTF-8 JSONL file or a directory that
contains JSONL files. A full validation opens those files locally to check the
record schema, duplicate titles and body hashes, dates, source-account identity,
stable article IDs, and raw-to-derived lineage. Its JSON report omits article
titles, bodies, URLs, content hashes, and absolute paths.

## Private-directory structure dry-run

Use this command before authorizing any content-level validation:

```powershell
python scripts\validate_article_corpus.py "<private-corpus-root>" --dry-run --pretty
```

The dry-run is strictly read-only and does not open any JSONL file. It recognizes
either `raw`/`derived` directories or the layered `l0_raw` through
`l6_prior_packs` corpus skeleton, skips symbolic-link files, and reports only
aggregate structural counts. It does not print filenames or write a report
file. For that reason, `--dry-run` cannot be combined with `--output`. Use
`--layout raw-derived` or `--layout layered` only when automatic detection is
not appropriate.

A successful dry-run does not establish corpus quality. Required fields, empty
bodies, duplicate hashes, dates, accounts, article IDs, and lineage remain
unchecked until a separately authorized full validation is run.

For a layered private knowledge store, run full validation only against an
authorized article-record JSONL export that follows this validator's raw/derived
schema. Do not point content-level validation at the entire L0-L6 root because
the other layers contain different record types.

## Full local validation

After confirming that the local corpus is authorized for content-level access:

```powershell
python scripts\validate_article_corpus.py "<authorized-corpus-root>" --as-of 2026-09-06 --pretty
```

Use `--output <local-report.json>` only when a retained local report is intended.
Exit code `0` means valid or valid with warnings, `1` means validation errors,
and `2` means an input or configuration error.
