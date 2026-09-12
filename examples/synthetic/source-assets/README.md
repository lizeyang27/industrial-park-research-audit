# Fully synthetic source assets

These text files are invented fixtures for testing optional byte-hash verification. They contain no real reviewer comment, meeting record, organization, person, or industry fact.

The paths in `../private-source-manifest.jsonl` are relative to the public Skill repository root. A manifest-only run must still report the assets as unverified. Passing the repository root with `--source-root` makes the builder read every listed fixture and compare its bytes with the declared SHA-256 digest.
