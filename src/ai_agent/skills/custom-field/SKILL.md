---
name: custom-field
description: Add custom fields and override standard field properties using Custom Field and Property Setter records.
risk: writes
governs: Custom Field, Property Setter
---

<!-- records:
Custom Field:
  - dt
  - label
  - fieldname
  - fieldtype
  - options
  - insert_after
  - reqd
  - read_only
  - hidden
Property Setter:
  - doc_type
  - doctype_or_field
  - field_name
  - property
  - property_type
  - value
DocType:
  - name
  - fields
-->

# Custom Field

Safely extend Frappe DocTypes by adding custom fields or adjusting properties of standard fields.

## Allowed Tools

* `get_document`: Inspect the target DocType schema to verify existing fields and find valid `insert_after` anchor fields.
* `list_documents`: Check for existing `Custom Field` and `Property Setter` records to prevent duplicate keys.
* `create_document`: Create `Custom Field` or `Property Setter` records after confirmation.
* `update_document`: Update existing `Custom Field` or `Property Setter` records after confirmation.

Do not edit DocType JSON files directly.

## Rules

1. **Choice of Mechanism**:
   * **Adding a new field**: Always create a `Custom Field` record.
   * **Modifying an existing standard field** (e.g. making it mandatory, hidden, or read-only): Always create a `Property Setter` record. Do not create a duplicate field with the same name.
2. **Schema Verification & Positioning**:
   * Before adding a custom field, inspect the DocType with `get_document(doctype="DocType", name=doc_type)`.
   * Ensure the generated `fieldname` does not clash with any existing field.
   * Pick an existing field for `insert_after` to guarantee correct visual placement on the form.
3. **Custom Field Specification**:
   * `dt`: Target DocType name (e.g. `Customer`).
   * `label`: Human-readable label (e.g. `Loyalty Tier`).
   * `fieldname`: Snake_case identifier (e.g. `loyalty_tier` or `custom_loyalty_tier`).
   * `fieldtype`: Valid Frappe field type (`Data`, `Select`, `Check`, `Currency`, `Date`, `Link`, etc.).
   * `options`: Dropdown options for `Select` (separated by newline `\n`) or target DocType for `Link`.
   * `insert_after`: An existing fieldname in `dt`.
4. **Property Setter Specification**:
   * `doc_type`: Target DocType (e.g. `Task`).
   * `doctype_or_field`: Set to `"DocField"`.
   * `field_name`: The field to adjust (e.g. `expected_end_date`).
   * `property`: Property name (`reqd`, `hidden`, `read_only`).
   * `property_type`: `"Check"`.
   * `value`: `"1"` to enable or `"0"` to disable.
5. **Confirmation**: Field changes alter database metadata and form layouts. Detail the exact DocType, fieldname, and positioning before calling the write tool.
