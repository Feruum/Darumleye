# Regulatory and internal policy context

The competition task asks for static review of its supplied requirements document. It suggests ISO/IEC/IEEE 29148:2018 (individual and set quality characteristics) and IEEE Std 830-1998 as the basis for a quality rubric. IEEE 830 is a superseded standard; retain it as a historical reference specifically mentioned by the organizers, not as a current compliance obligation. The competition does not ask the agent to certify legal compliance or infer an organization's internal policy. See the [task](https://github.com/BCC-HUB/ai-requirements-validator-task), [ISO's standard overview](https://www.iso.org/standard/72089.html) and [IEEE's status](https://standards.ieee.org/ieee/830/1222/).

## Recommended source sets

| Source set | Purpose | Use in competition report |
| --- | --- | --- |
| Organizer-provided specification | Evaluate its requirements, relationships, and acceptance criteria | Primary evidence for every document finding |
| Requirements engineering rubric | Define the properties of clear and verifiable requirements | Evaluation criteria, not claims about the bank |
| Internal bank policies: approved business rules, API conventions, information-security policy, QA/acceptance template, operational SLA | Compare proposed behavior with the organization's own requirements | Only if explicitly supplied as approved reference documents |
| Applicable public legislation and regulatory acts | Identify questions for a compliance or legal specialist | Independent reference check, not a certification or a substitute for source citations |

The reference mode exposes five categories: `quality_standard`, `bank_policy`, `api_testing`, `payments_data`, and `security_regulation`. For API and testing references, also consider the [OpenAPI Specification](https://spec.openapis.org/oas/) with the project's actual version, and [ISO/IEC/IEEE 29119-2:2021](https://www.iso.org/standard/79428.html) for test processes. Neither substitutes for an organization's approved internal standards.

For a Kazakhstan banking deployment, these are candidate public references to screen for applicability. The links were checked on 2026-11-25. They are references for discovery, not a determination that every provision applies to the organizer's training case.

- [Law on payments and payment systems, No. 11-VI](https://adilet.zan.kz/rus/docs/Z1600000011).
- [Law on personal data and their protection, No. 94-V](https://old.adilet.zan.kz/rus/docs/Z1300000094).
- [Requirements for information security in the financial sector, registered No. 38336](https://old.adilet.zan.kz/rus/docs/V2600038336).
- [Requirements for continuity of operation of digital infrastructure, registration No. 38303](https://old.adilet.zan.kz/rus/docs/V2600038303).

Check the current official revision, effective date, scope, and applicability before using a clause. Do not automatically assume a general standard, a regulator's rule, or an internal bank policy applies to an educational card-notification feature. Internal documents must come from the authorized owner; do not fabricate BCC's policies.

## Implemented reference review

The optional reference mode works with explicitly selected source documents. Each source has a category, title, issuer, scope, version/effective date, optional source URL and SHA-256 hash computed from the actual uploaded bytes. The standard-based quality rubric is always present in the core review; copyrighted standards are not reproduced in this repository. The source catalog links to official publications; the comparison uses the actual authorized PDF, TXT, MD, DOCX, XLSX or CSV file. The app does not fetch arbitrary URLs or guess current editions. Selected source chunks are indexed in a dedicated persistent Chroma collection. Each query is restricted to the authenticated owner and exact hashes of the currently selected documents; a re-upload of the same content reuses indexed chunks. The form currently selects by upload, not by a saved-source catalog. Uploaded specification and reference texts are sent to the configured LLM for the comparison; use only source files authorized for that provider. The internal BCC policies and licensed ISO texts were not supplied. A licensed OpenAPI 3.1.1 excerpt is included in [references](references/README.md). Four official Әділет HTML snapshots, metadata pages and selected text excerpts were saved locally under the ignored `.artifacts/regulatory-sources/` directory on 2026-11-25. The mirror response was cached; its displayed revision is not proof of the current effective text. Confirm the official edition and applicability before relying on these excerpts. The local `public-reference-manifest.json` loads the five public excerpts; `snapshot-provenance.json` records their sources, selection ranges and SHA-256 hashes. These snapshots are local evidence, not committed legal source files.

From the workspace **Проверка требований**, expand the optional reference-document section. Choose a category or source preset, upload the selected reference text, confirm its metadata and start the normal review. The report lists each supplied source, retrieval coverage (selected/total chunks and characters), its applicability assessment, and separate candidate observations. Each requirement drives hybrid search through Chroma and BM25, with RRF fusion. When no embedding model is explicitly configured, deterministic local lexical feature vectors are used, not trained semantic embeddings. When Nace is enabled, Drex optionally reranks the candidate passages and independently evaluates each RF-* observation against the original document and its cited reference. Each observation has an exact citation in the submitted specification and a second exact citation in the reference; the code verifies both against the respective files. Observations distinguish a potential discrepancy, missing evidence, and unclear applicability. A reviewer confirms applicability and conclusions. Neither the reference comparison nor its failure recalculates the primary quality verdict or Drex's independent core finding assessment. Partial source coverage is never a definitive non-applicability or compliance decision.

## CLI

Create a JSON array whose entries specify `category`, `title`, `issuer`, `version`, `scope`, `source_url` (HTTP(S) URL or `null`) and `path` (relative to the manifest). Each path is one explicitly authorized reference file. See [template for actual source files](examples/reference-manifest.template.json), which must be edited and populated before use. The [five-category synthetic demo manifest](examples/reference-manifest.example.json) is immediately runnable with the existing synthetic payment specification and a configured LLM; all its texts are fictional, not bank policy, ISO text or legislation.

From the repository root:

```bash
uv run --project vibe/services/agent --frozen python -m agent.spec_review_cli --input vibe/docs/examples/fintech-payment-spec.txt --output .artifacts/reference-demo.md --reference-manifest vibe/docs/examples/reference-manifest.example.json --env-file vibe/.env
```

This example uses the current `multi_agent` default with GPT-6 Luna in the example configuration. Pass `--review-mode legacy` to run the original baseline instead. Reference observations remain separate from the core verdict in either mode.

To use the five public excerpts saved in this workspace (if available), replace the manifest argument above with:

```bash
--reference-manifest .artifacts/regulatory-sources/public-reference-manifest.json
```

The example selection covers payment cards (articles 39, 39-1, 40), personal data (articles 5, 7, 11, 12, 22), financial information security (chapters 3–5 and chapter 9, paragraph 8), the continuity requirements appendix, and selected OpenAPI operation and response sections. This is a bounded research corpus, not the full legislation. It does not imply a rule applies to the case. When adding further sources, select smaller applicable excerpts to stay within the aggregate text budget.

For a real review, use the organizer's input file and your actual approved source manifest. Omit `--reference-manifest` for the official document-only report. Select applicable excerpts when a full law or standard is too long; record the original source title, edition and the excerpt's scope. Limits: 8 reference files, 5 MiB each, 10 MiB across reference files, up to 24,000 extracted characters and 60 PDF pages per file (bounded local OCR for pages without text), 48,000 extracted characters across references. The Chroma retrieval returns at most 12,000 original reference characters and reports any partial coverage. The optional comparison uses the remaining overall review budget (at most 85 seconds); a failed or incomplete comparison reports `unavailable` for the selected sources and preserves the primary report.

Exa may discover official sources in a separate research mode. It is not required for the hackathon run; an exact approved corpus is more reproducible than unrestricted web search. Do not send private requirements as a search query to a public web-search provider. Drex checks core finding support in the original specification and RF-* observation support across both original documents; it does not certify legal or regulatory conformity.
