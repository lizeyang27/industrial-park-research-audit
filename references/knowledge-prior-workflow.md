# Knowledge-prior workflow

Use this workflow only when the review must demonstrate that authorized knowledge shaped the questions **before** the draft was read. It creates a local, hash-bound audit trail; it does not prove that a model learned expertise, and it does not turn a heuristic into evidence.

This manifest is an atomic question set, not a complete reading framework. When the task also requires pre-draft typed variable, mechanism, actor-decision, expected-evidence, and falsifier nodes, keep this manifest unchanged and add the separate [reading-framework workflow](reading-framework-workflow.md). The current manifest does not encode variable-tree edges or an evidence matrix. When source/support context is required, add a sidecar governed by [private experience provenance](private-experience-provenance.md); do not put provenance into the C-group manifest.

## Public and private boundary

The public repository contains schemas, deterministic scripts, documentation, tests, and synthetic fixtures. Keep all of the following outside its Git history:

- real Knowledge Cards and source manifests;
- authorization records for real people or organizations;
- generated prior manifests, draft receipts, and issue registers from real reviews;
- draft text, excerpts, filenames, paths, URLs, comments, identities, and model transcripts.

A prior manifest may contain authorized abstractions from private cards. It is therefore a private review artifact even though it omits raw provenance. Standard output prints only anonymous artifact IDs, hashes, counts, status codes, and privacy flags.

The builder does not redact or generalize card prose. It assumes `expert_heuristic` and `control` bodies were already abstracted, reviewed, and covered by the authorization record. If a card contains a name, quotation, client detail, path, or raw meeting wording, quarantine and rewrite the card before using this workflow.

## State sequence

```text
task metadata recorded
        |
        v
TaskEnvelope -- authorization + metadata-only routing --> PriorQuestionManifest
        |                                                   |
        |                                        hash and timestamp fixed
        |                                                   |
        +--------------------- then ------------------------+
                                                            v
                                                      draft ingested
                                                            |
                                                   DraftReceipt created
                                                            |
                                                            v
                                             issues get discovery_origin
                                                            |
                                                            v
                                                  trace validation
```

The sequence is fail-closed. If the draft was already visible, visibility is unknown, or any routing field came from `draft_text`, set `precommit_possible: false`; the `prior` command refuses to manufacture a pre-draft claim.

## Artifacts

### `TaskEnvelope`

Create `references/task-envelope.schema.json`-compatible metadata before opening the draft. It records the review mode, domain codes, article type, stage, audience, jurisdiction, entity type, as-of date, available input roles, allowed discovery channels, and the provenance of each routing field. It deliberately has no draft text, topic excerpt, source URL, personal name, or local path field.

The authorization context declares four separate decisions:

- `authorization_id`: the exact local authorization record;
- `purpose`: an allowed use shared by the authorization and each private card;
- `processor_class`: `local_deterministic` or, only when expressly granted, `model_context_abstract`;
- `output_audience`: `private_review` for this P0 workflow.

### `PriorQuestionManifest`

`knowledge_base.py prior` considers only current, active `expert_heuristic` and `control` cards whose structured scope matches the envelope. It does not accept a keyword query and does not read a draft. Each selected card becomes one review prompt with:

- trigger scope (`applicability` and `exclusions`);
- an atomic review question;
- issue/risk family;
- recommended discovery channels and verification action;
- counterhypothesis prompts and a closure condition;
- `knowledge_role: review_prompt` and `eligible_as_evidence: false`.

Selection is deterministic, capped globally and per issue family, and bound to hashes of the task envelope, card file, and authorization file. The manifest excludes titles, source IDs, locators, source paths, and draft text.

### `DraftReceipt` and issue origins

After the manifest exists, `prior_trace.py bind-draft` hashes the local draft and records only its byte count, hash, ingest time, and the prior manifest binding. It never stores or prints the draft filename, path, or body.

Each later issue must declare one discovery origin. `prior_knowledge` requires a `prior_question_id` that existed in the manifest. A post-draft origin such as `draft_text`, `document_set`, `web`, `expert`, or `human_review` cannot borrow a prior question ID. The first-observed time cannot precede draft ingest.

## Authorization file

The local JSON Lines authorization record must include:

```json
{
  "authorization_id": "AUTH-EXAMPLE-001",
  "status": "active",
  "allowed_uses": ["local-review"],
  "allowed_processors": ["local_deterministic", "model_context_abstract"],
  "output_audiences": ["private_review"],
  "abstract_guidance_allowed": true,
  "external_upload_allowed": false,
  "public_export_allowed": false,
  "valid_from": "2026-01-01",
  "expires_at": "2099-12-31"
}
```

`model_context_abstract` needs the explicit `abstract_guidance_allowed: true` grant. Missing `allowed_processors` and `output_audiences` are accepted only for legacy compatibility and default to `local_deterministic` and `private_review`. Missing, duplicate, inactive, not-yet-valid, expired, wrong-purpose, wrong-processor, or wrong-audience records are denied.

## Commands

The public fixtures are synthetic. Write generated artifacts to a temporary or ignored private directory:

```powershell
python scripts\knowledge_base.py prior `
  examples\synthetic\knowledge-cards.jsonl `
  examples\synthetic\task-envelope.json `
  --authorizations examples\synthetic\authorizations.jsonl `
  --output work\prior-manifest.json `
  --created-at 2026-09-06T01:01:00Z

python scripts\prior_trace.py bind-draft `
  work\prior-manifest.json <local-draft> `
  --output work\draft-receipt.json `
  --ingested-at 2026-09-06T01:02:00Z

python scripts\prior_trace.py validate `
  work\prior-manifest.json `
  work\draft-receipt.json `
  examples\synthetic\prior-issue-register.json
```

Do not pass a real private manifest to an external model unless its authorization explicitly permits abstract model context and the configured processor is actually within that grant. Do not use `--force` unless replacing the exact intended local artifact is authorized.

## What the trace can and cannot show

The trace can show that a particular metadata envelope, authorization snapshot, and Knowledge Card snapshot produced a fixed question set before a particular draft hash was ingested. It can also test whether later issues truthfully reference that set.

It cannot prove that nobody saw the draft elsewhere, that a question caused a correct finding, that the knowledge card is true, or that a reported issue is resolved. Timestamps and hashes are locally generated and unsigned; a person who can rewrite every artifact can also recreate the chain. Use an append-only or signed log if adversarial tamper resistance is required. Evidence admission, current-fact verification, counterevidence, and human closure remain separate controls.
