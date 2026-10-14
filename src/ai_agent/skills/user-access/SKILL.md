---
name: user-access
description: Modify user roles, account active status, and user permissions while preventing lockout of the final System Manager.
risk: always-ask
governs: update_document:User, create_document:User Permission, update_document:User Permission
---

<!-- records:
User:
  - name
  - enabled
  - roles
Role:
  - name
Has Role:
  - role
User Permission:
  - user
  - allow
  - for_value
  - is_default
  - apply_to_all_doctypes
-->

# User Access

Manage user activation, role assignments, and document permissions.

## Allowed Tools

* `get_document`: Fetch the existing `User` record with its current roles.
* `list_documents`: Find users, verify existing System Managers, or check permissions.
* `update_document`: Update user `enabled` status or complete `roles` child table.
* `create_document`: Create a `User Permission` record.
* `delete_document`: Remove a `User Permission` record.

Do not use raw SQL or shell commands.

## Rules

1. **Read Before Modifying Roles**: In Frappe, the `roles` child table is completely replaced during an update, never merged. You must first fetch the user with `get_document(doctype="User", name=user_name)` to retrieve the current roles, calculate the additions or removals, and supply the full updated list.
2. **Never Remove the Last System Manager**:
   * Before removing the `System Manager` role or setting `enabled: 0` on a System Manager, check if other active System Managers exist using `list_documents(doctype="User", filters={"enabled": 1}, fields=["name"])` alongside `Has Role` checks.
   * If the user is the sole remaining active System Manager, refuse the action and explain the lockout risk.
3. **Role Validation**: When adding roles, verify each role name exists using `list_documents(doctype="Role", filters={"name": role_name})`.
4. **User Permissions**:
   * Scopes a user's visibility for a specific DocType value (e.g. restrict to a Company or Territory).
   * Fields: `user` (User email), `allow` (DocType name like `Company`), `for_value` (record name like `Acme Ltd`), `is_default` (0 or 1), `apply_to_all_doctypes` (1).
5. **Always-Ask Confirmation**: Privilege changes are high risk. Always present the before and after role comparison clearly in the response.

## Role Update Example

To add `Purchase User` to Morgan while keeping existing roles:
1. `get_document(doctype="User", name="morgan@supply.example.org")`
   Result: `{"roles": [{"role": "Employee"}, {"role": "Desk User"}]}`
2. Compute new list: `[{"role": "Employee"}, {"role": "Desk User"}, {"role": "Purchase User"}]`
3. Call `update_document(doctype="User", name="morgan@supply.example.org", data={"roles": ...})`
