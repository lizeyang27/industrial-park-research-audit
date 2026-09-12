# Storage capacity audit

Use `scripts/audit_storage_budget.py` to measure the public Skill package or an authorized layered corpus before deciding whether to expand a sample. The command reads directory entries and file sizes only. It does not open, hash, parse, or print file content.

## Profiles

- `public-skill` reports fixed aggregate groups for root files, agents, assets, docs, examples, references, scripts, tests, and other directories.
- `layered-corpus` reports fixed aggregate groups for L0 through L6 plus support files. It recognizes `l0_raw`, `l1_index`, both `l2_article_maps`/`l2_maps`, both `l3_cards`/`l3_patterns`, both `l4_clusters`/`l4_contradictions`, both `l5_routes`/`l5_rubrics`, and both `l6_prior_packs`/`l6_policy`. Missing or empty layers are valid and report zero. Pass the actual corpus root rather than a parent wrapper directory.

The report includes total file count, total bytes, largest-file bytes, and the same metrics per fixed group. It never emits filenames, titles, local paths, content, or file hashes. Unknown public directories are collapsed into `other`; non-layer corpus files are collapsed into `support`.

## Commands

Audit the public Skill tree:

```powershell
python scripts\audit_storage_budget.py . `
  --profile public-skill `
  --soft-budget-bytes 5000000 `
  --hard-budget-bytes 10000000 `
  --pretty
```

Project a synthetic or separately authorized corpus sample to a target article count:

```powershell
python scripts\audit_storage_budget.py path\to\layered-corpus `
  --profile layered-corpus `
  --sample-articles 10 `
  --target-articles 200 `
  --soft-budget-bytes 50000000 `
  --hard-budget-bytes 100000000 `
  --pretty
```

Supply `--sample-articles` and `--target-articles` together. The projection scales every observed group by the same article-count ratio using integer ceiling. This is a transparent planning approximation, not a storage guarantee: fixed metadata, attachments, chunk counts, article length, filesystem allocation, compression, indexes, and future derived layers may scale differently.

## Budget behavior

The budget applies to projected total bytes when projection is enabled and observed total bytes otherwise.

- `STORAGE_SOFT_BUDGET_EXCEEDED`: warning; the command exits successfully so planning can continue.
- `STORAGE_HARD_BUDGET_EXCEEDED`: blocked; exit code 1.
- `BUDGET_CONFIGURATION_INVALID`: blocked when the soft limit exceeds the hard limit.
- `PROJECTION_ARGUMENTS_INCOMPLETE`: blocked when only one article-count argument is supplied.
- `ROOT_NOT_DIRECTORY` or `METADATA_SCAN_INCOMPLETE`: blocked because the aggregate is not reliable.
- `SYMLINK_SKIPPED`: warning; symlinks are not followed or counted.

Unexpected operational failures return only `STORAGE_AUDIT_FAILED`. All standard output remains aggregate-only, including failure paths.

This tool measures capacity, not lineage or semantic correctness. Continue to use `validate_layered_corpus.py` for hashes, locators, cross-layer references, evidence boundaries, and approval state.
