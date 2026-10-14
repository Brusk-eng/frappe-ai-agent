---
name: print-format
description: Create and edit custom Jinja print formats for Frappe DocTypes with verified fields and child table loops.
risk: writes
---

<!-- records:
Print Format:
  - name
  - doc_type
  - module
  - custom_format
  - print_format_type
  - raw_printing
  - html
  - disabled
DocType:
  - name
  - fields
  - module
-->

# Print Format

Create and modify custom Jinja print formats for Frappe DocTypes.

## Allowed Tools

* `get_document`: Inspect DocType schema and read existing Print Format records.
* `list_documents`: Find sample documents and check for existing Print Format names.
* `create_document`: Create a new Print Format record after confirmation.
* `update_document`: Update an existing Print Format record after confirmation.

Do not use any other tools for this task.

## Rules

1. **Inspect Before Writing**: Always call `get_document` on DocType "DocType" for the target document type. Read its actual field definitions (`fieldname`, `fieldtype`, `label`). Never invent or guess field names.
2. **Jinja Templating**:
   * Refer to document fields using `{{ doc.fieldname }}`.
   * Format dates using `{{ frappe.utils.format_date(doc.date_field) }}` or currency with `{{ frappe.format_value(doc.amount, doc.meta.get_field('amount'), doc) }}`.
   * For child tables, iterate with `{% for row in doc.child_table_name %} ... {% endfor %}`.
3. **Record Shape**:
   * `name`: Descriptive unique name (e.g. `Custom Sales Invoice Modern`).
   * `doc_type`: Target DocType (e.g. `Sales Invoice`).
   * `module`: Standard module matching the DocType or `Custom`.
   * `custom_format`: Set to `1`.
   * `print_format_type`: Set to `"Jinja"`.
   * `raw_printing`: Set to `0`.
   * `html`: Clean, responsive HTML with inline CSS or `<style>` block.
4. **Duplicate Prevention**: Before calling `create_document`, check if a Print Format with the target name already exists using `list_documents`. If it exists, update it or suggest an alternate name.
5. **Preview and Confirmation**: Print format writes create or change records and require user confirmation. Describe the layout clearly in the text block alongside the tool call.

## HTML Structure Template

```html
<div class="print-format">
  <div class="header">
    <h2>{{ doc.name }}</h2>
    <p>Date: {{ doc.posting_date or doc.transaction_date or "" }}</p>
  </div>
  <table class="table table-bordered">
    <thead>
      <tr>
        <th>Item</th>
        <th class="text-right">Qty</th>
        <th class="text-right">Rate</th>
        <th class="text-right">Amount</th>
      </tr>
    </thead>
    <tbody>
      {% for item in doc.items %}
      <tr>
        <td>{{ item.item_name or item.item_code }}</td>
        <td class="text-right">{{ item.qty }}</td>
        <td class="text-right">{{ item.rate }}</td>
        <td class="text-right">{{ item.amount }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

## Example Walkthrough

1. User requests: "Create a modern print format for Quotation called Quotation Modern".
2. Assistant calls `get_document(doctype="DocType", name="Quotation")` to discover field names (`party_name`, `transaction_date`, `valid_till`, `items`).
3. Assistant checks `list_documents(doctype="Print Format", filters=[["name", "=", "Quotation Modern"]])`.
4. Assistant prepares the Jinja template and calls `create_document` with `doctype="Print Format"`, `doc_type="Quotation"`, `custom_format=1`, `print_format_type="Jinja"`.
