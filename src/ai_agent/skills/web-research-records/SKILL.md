---
name: web-research-records
description: Research public entities on the web, verify facts, deduplicate against existing records, and create ERPNext records.
risk: writes
---

<!-- records:
DocType:
  - name
  - fields
-->

# Web Research Records

Perform web research on external organizations or contacts, verify facts from source pages, check for existing records, and create verified Frappe / ERPNext documents.

## Allowed Tools

* `web_search`: Search for official public websites, registries, or company profiles.
* `fetch_web_page`: Read source web pages to extract genuine addresses, emails, and contact details.
* `get_document`: Inspect the target DocType schema to verify fieldnames (e.g. `Lead`, `Customer`, `Supplier`).
* `list_documents`: Check for existing records to prevent duplicates before creation.
* `create_documents`: Create several verified records of one doctype in one call after the user approves the batch.
* `create_document`: Create one verified document record per turn after user confirmation.
* `update_document`: Update an existing record with newly discovered information after user confirmation.

Do not use unlisted tools or fabricate details.

## Rules

1. **Read the Source Page**: Do not rely solely on `web_search` snippets. Always call `fetch_web_page` on the official page to verify physical addresses, registered company names, and contact emails.
2. **Authentic Organization Website**: The website field on a created record must hold the organization's own primary web domain, never the directory, aggregator, or search engine result that mentioned it.
3. **No Fabricated Addresses or Contact Info**: Never guess postal codes, phone numbers, or corporate emails. If a contact field is not explicitly present on the verified page, leave the field empty and note the omission in the summary.
4. **Duplicate Prevention**: Before creating any record, search existing documents using `list_documents` filtering on the company name, website, or primary email. If a matching record already exists, report its existence and offer an update rather than duplicating.
5. **One Batch, Not One By One**: When the research yields several new records of the same doctype, verify every row first (sources read, duplicates checked), then create them in one `create_documents` call with up to 100 rows, so the user approves the batch once. Use `create_document` for a single record, and one `update_document` per reply for existing ones.
6. **Summary Table**: Conclude the turn with a clean markdown table listing all entities processed, their status (Created, Updated, Already Exists, Skipped), and the exact source URL consulted.

## Example Summary Format

| Organization | Action | Record Name | Source URL | Notes |
| --- | --- | --- | --- | --- |
| Apex Logistics | Created | LEAD-2026-001 | https://apex.example.org | Verified office in Portland |
| Beacon Freight | Already Exists | LEAD-2026-000 | https://beacon.example.org | Skipped duplicate record |
