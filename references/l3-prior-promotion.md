# L3-to-prior human approval gate

This gate converts an authorized private L3 abstraction into the Knowledge Card interface consumed by `scripts/knowledge_base.py prior`. It does not publish the card, approve an external fact, or copy a private knowledge base into this repository.

## Decision rule

A record is promotable only when every condition below is true:

1. `card_type` is `expert_heuristic` or `control`.
2. `human_review.status` is exactly `accepted`. Machine creation, sampling, or an `unreviewed` state is insufficient.
3. `lifecycle.status` is `active`.
4. The card has an abstract boundary: `inference` or `opinion` for a heuristic; `process_control`, `inference`, or `opinion` for a control.
5. `rights_ref` resolves to an active authorization that is in date.
6. The authorization explicitly has `abstract_guidance_allowed: true`, permits `local-review`, permits the `model_context_abstract` processor, and permits `private_review` output.
7. The minimum L3 fields needed for a complete, traceable conversion are present.

`reported_statement` and `fact_evidence` are always skipped, even if a human accepted the underlying L3 record. An unreviewed record is always skipped. A zero-card output is therefore a valid and often desirable governance result.

## Non-elevation invariant

Every promoted card remains one of two review-prompt types: `expert_heuristic` or `control`. The converter fixes `epistemic.status` to `reported`, never emits a factual boundary, marks the card private and non-exportable, and adds an explicit limitation that the prompt is not factual evidence. A source's self-description cannot become a fact through this route.

The private output preserves the source L3 record ID, corpus ID, parent IDs, input object IDs, article IDs, source-version IDs, chunk IDs, independent-source IDs, complete structured locators, transformation method, and a canonical input hash. Those values are written only to the private output; standard output never shows them.

## Run locally

```powershell
python scripts\promote_l3_priors.py <private-l3-cards.jsonl> `
  --authorizations <private-authorizations.jsonl> `
  --output <private-or-ignored-output.jsonl> `
  --at-date 2026-09-06
```

The output must be outside the public repository or under one of its ignored private-work roots, such as `work/` or `private-kb/`. Existing output is not overwritten unless `--force` is supplied.

Standard output contains only:

- overall status;
- input, promoted, and skipped counts;
- aggregate issue-code counts;
- a fixed privacy marker.

It omits card IDs, titles, card bodies, source IDs, locators, local filenames, and paths. Common skip codes include `CARD_TYPE_NOT_PROMOTABLE`, `HUMAN_REVIEW_NOT_ACCEPTED`, `LIFECYCLE_NOT_ACTIVE`, `ABSTRACT_BOUNDARY_REQUIRED`, `AUTHORIZATION_NOT_FOUND`, `AUTHORIZATION_NOT_ACTIVE`, and `ABSTRACT_GUIDANCE_DENIED`.

## Downstream use

Validate or pass the resulting private JSON Lines file to `knowledge_base.py prior` with the same live authorization list. Promotion and prior routing are separate controls: promotion confirms that a human accepted an abstract prompt; prior routing still checks task authorization, scope, freshness, processor class, private audience, and pre-draft chronology.

Use only the fully synthetic candidates and authorizations under `examples/synthetic/` in public tests. Never turn private L3 records into repository fixtures, screenshots, documentation examples, or test snapshots.
