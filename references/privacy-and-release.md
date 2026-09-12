# Privacy and public-release boundary

This project must be released from a clean-room copy. The public repository is a reusable research-review tool, not a repository of the private materials that inspired it.

## 1. Clean-room rule

- Keep the private corpus and the public repository in different directories and different Git histories.
- Carry over only general requirements, control ideas, and independently written implementation code.
- Do not copy sentences, comments, tables, screenshots, filenames, figures, URLs, layouts, branding, or document metadata from private materials.
- Build demonstrations from zero with fictional entities, invented values, reserved example domains, and an explicit `synthetic` label.
- Confirm that the author owns the publishable code and documentation and that employment, internship, confidentiality, database, and third-party license terms permit publication.
- A public license applies only to material the repository owner has the right to license. It does not grant rights to excluded employer or third-party material.

## 2. Publish by allowlist

Only these categories should enter the public repository:

- `SKILL.md` and interface metadata written for the public skill.
- Independently written scripts and tests.
- General control guidance and privacy documentation.
- Fully synthetic fixtures and expected outputs.
- Dependency declarations, license text, provenance notes, and third-party notices.

Everything else remains private unless it is deliberately reviewed and added to the allowlist. In particular, do not publish:

- Source or draft Office documents, PDFs, images, extracted text, screenshots, or renderings.
- Reviewer comments, tracked changes, author names, timestamps, or revision history.
- Real organization, client, project, product, speaker, colleague, or mentor names.
- Internal topic plans, editorial calendars, prompts, model transcripts, or private share links.
- Raw or compiled datasets, proprietary database exports, report layouts, or branded charts.
- Exact private-corpus counts, hashes, file sizes, local filenames, or run logs.
- Existing generated registers or reports produced from the private corpus.
- Resumes, contact details, account identifiers, or other personal files.

Use explicit staging such as `git add SKILL.md scripts/...` and inspect the staged diff. Do not use a catch-all staging command for a release.

## 3. Local-first data handling

- Process user files locally by default.
- Do not upload documents, extracted text, URLs, or review comments to an external model or service without the user's explicit authorization for that transfer.
- Minimize retained data. Public-safe output should prefer anonymous material IDs, counts, categories, and human-review status over raw excerpts.
- Keep any local path-to-ID mapping outside the public repository.
- Treat an exception as a review signal, not as a factual or professional conclusion.

## 4. Common leakage channels

### Office packages and comments

Office files are ZIP-based containers that may retain creator, last editor, company, custom properties, comments, tracked revisions, hidden sheets, external links, and document thumbnails. Removing visible text or exporting a new copy is not proof that these fields disappeared. Public examples should be generated from scratch; real Office files should not be included.

### PDF and image metadata

PDF metadata, embedded attachments, annotations, OCR layers, and image EXIF fields may reveal names, devices, locations, software, or prior content. A cropped screenshot can also retain proprietary layout and data. Prefer newly generated synthetic text fixtures to binary examples.

### Paths and messaging exports

Absolute paths can expose a workstation username and folder structure. Messaging-client export folders and account identifiers can identify a private account. The preflight scanner therefore rejects Windows user-directory paths and common instant-messaging export markers. Documentation should describe these patterns without reproducing a real path.

### URLs and private sessions

AI-conversation links, collaboration-document links, message archives, cloud-drive links, and URLs containing access tokens or signatures can grant access or reveal browsing and research history. Replace them with reserved example domains. If a private link was exposed, disable or rotate the share link before addressing Git history.

### CSV formula injection

Spreadsheet applications may interpret a CSV cell beginning with `=`, `+`, `-`, or `@` as a formula. Filenames, comments, URLs, and other untrusted values must be neutralized before export, for example by storing a safe typed representation or prefixing the value with an apostrophe. The release scanner treats a dangerous leading character as a failure requiring review.

### Names and organizations

Person and organization names vary by corpus and cannot be identified safely with a universal built-in list. Maintain a private denylist outside the repository and pass it to the scanner. Use one literal term per line, or prefix a regular expression with `re:`. Never commit the denylist if it contains real names.

## 5. Pre-release procedure

1. Create a new repository directory that has never contained the private corpus.
2. Copy or write only allowlisted public files into it.
3. Keep the private name and organization denylist outside the repository.
4. Run:

   ```powershell
   python scripts/preflight_public.py . --denylist ..\private-denylist.txt
   ```

5. Resolve every finding. Do not suppress a finding merely to make the check pass.
6. Review the exact tracked-file list and staged diff before the first commit.
7. Test in a private remote repository first, including generated artifacts and automation logs.
8. Make the repository public only after a second human review.

The scanner returns `0` only when no findings remain, `1` when it detects a release blocker, and `2` for an invalid configuration or an incomplete scan.

## 6. If something sensitive is committed

- Stop publishing and disable or rotate any exposed access-bearing link or credential.
- Removing a file in a later commit is not sufficient because Git retains history.
- Rebuild the repository from a clean directory when practical. If it was already pushed or forked, follow the hosting provider's sensitive-data history-removal process and assume clones or cached copies may persist.
- Record the incident privately and add a regression pattern or test that prevents recurrence.

## 7. Public positioning

Describe the repository as an independent, human-in-the-loop research evidence review tool. State that it is not affiliated with or endorsed by any employer or client; contains no employer documents or production data; and does not provide an automated fact-check, audit opinion, legal conclusion, or professional assurance service.
