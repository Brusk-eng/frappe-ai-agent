---
name: letter-head
description: Design and configure HTML letter heads and footers for PDF documents with verified styling and default rules.
risk: writes
governs: Letter Head
---

<!-- records:
Letter Head:
  - letter_head_name
  - source
  - content
  - footer_source
  - footer
  - is_default
  - disabled
-->

# Letter Head

Create and manage Frappe Letter Head documents with HTML headers and footers for printed reports and PDFs.

## Allowed Tools

* `list_documents`: Check existing Letter Heads and find current default.
* `create_document`: Create a new Letter Head after confirmation.
* `update_document`: Update content or default status of a Letter Head after confirmation.

Do not use raw file system tools or execute scripts.

## Rules

1. **HTML Source Mode**: Set `source: "HTML"` and `footer_source: "HTML"`. Use clean, inline-styled semantic HTML suitable for wkhtmltopdf.
2. **No Invented Image Addresses**: Never invent or hallucinate logo image URLs (e.g. `http://example.com/logo.png`). If the user has not provided a specific image URL or uploaded file path, use structured text styling (organization name, address, tax ID) with typographic hierarchy.
3. **Single Default Enforced**: Only one Letter Head should be marked `is_default: 1`. Before creating or setting a default, check existing defaults via `list_documents(doctype="Letter Head", filters={"is_default": 1})`. If another letter head is currently default, explain that setting the new one as default will supersede it.
4. **Duplicate Prevention**: Before creating, check whether `letter_head_name` already exists using `list_documents`.
5. **Layout Guidelines**:
   * Header height should be bounded (typically 60-100px).
   * Flexbox or standard table-based alignment works best across PDF rendering engines.
   * Footer typically contains company registration details, VAT numbers, and page numbers (`<span class="page"></span> of <span class="topage"></span>`).

## Example Header HTML

```html
<div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #3b82f6; padding-bottom: 12px; margin-bottom: 16px;">
  <div>
    <h1 style="margin: 0; font-size: 24px; color: #1e293b; font-family: sans-serif;">Acme Industrial Supplies</h1>
    <p style="margin: 4px 0 0 0; font-size: 12px; color: #64748b;">Precision Parts & Logistics</p>
  </div>
  <div style="text-align: right; font-size: 11px; color: #475569; line-height: 1.4;">
    <p style="margin: 0;">100 Innovation Way, Suite 400</p>
    <p style="margin: 0;">contact@acme.example.org | +1 (555) 019-2831</p>
    <p style="margin: 0;">Tax ID: TAX-99281-US</p>
  </div>
</div>
```
