---
name: client-script
description: Write client-side form scripts in Frappe with plain-words explanation, full code display, and disable instructions.
risk: always-ask
---

<!-- records:
Client Script:
  - name
  - dt
  - view
  - script
  - enabled
DocType:
  - name
  - fields
-->

# Client Script

Author small, robust JavaScript form scripts that execute in the Frappe Desk browser UI.

## Allowed Tools

* `get_document`: Inspect the target DocType schema to verify field names and child tables.
* `list_documents`: Check existing Client Scripts on the target DocType to avoid conflicting handlers.
* `create_document`: Create a new `Client Script` record after confirmation.
* `update_document`: Modify an existing `Client Script` record after confirmation.

Do not write raw assets or files to disk.

## Rules

1. **Security Classification**: Client scripts execute active JavaScript inside every Desk user's browser. This is classified as `always-ask` risk.
2. **Plain-Words Explanation First**: Before staging any script, explain in simple, accessible language exactly what the script does, which events trigger it (`refresh`, `validate`, `field_name`), and what side effects occur.
3. **Show Complete Code**: Never truncate or summarize the JavaScript. Always display the entire code listing in a markdown block so the administrator can inspect every line.
4. **How to Disable Instructions**: Every client script recommendation must conclude with clear emergency disabling instructions:
   > "To disable this script: Open Desk > Client Script list > Select this script > Uncheck 'Enabled' and Save."
5. **Verified Field Names**: Inspect the target DocType using `get_document(doctype="DocType", name=dt)` before writing script logic. Use actual fieldnames in `frm.doc.fieldname` and `frm.set_value()`.
6. **Standard Frappe Event Pattern**:
   ```javascript
   frappe.ui.form.on('DocType Name', {
       refresh(frm) {
           // On form load or refresh
       },
       field_name(frm) {
           // When a specific field changes
       },
       validate(frm) {
           // Before saving
       }
   });
   ```
7. **No Network Exfiltration**: Never include `fetch()`, `XMLHttpRequest`, or external script loading tags pointing outside the current Frappe origin.
