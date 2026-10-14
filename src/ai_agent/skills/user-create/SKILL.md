---
name: user-create
description: Create a Frappe user with a welcome email verification link and assigned roles without accepting passwords.
risk: always-ask
governs: create_document:User, create_documents:User
---

<!-- records:
User:
  - email
  - first_name
  - last_name
  - send_welcome_email
  - roles
  - enabled
Role:
  - name
Has Role:
  - role
-->

# User Create

Create new Frappe users safely with role assignments and passwordless onboarding.

## Allowed Tools

* `list_documents`: Check if the email already exists, and verify that requested roles exist.
* `create_document`: Create the `User` record with `send_welcome_email=1`.

Do not use delete or direct password-modifying tools.

## Rules

1. **Never Handle Passwords**: Never ask for, accept, or set a password in the chat. Chat logs are persisted and transmitted to language models.
2. **Onboarding by Email**: Always set `send_welcome_email: 1` so Frappe sends an activation email with a secure link allowing the user to set their own password.
3. **Duplicate Check**: Before creating, check whether a user with that email already exists via `list_documents(doctype="User", filters={"email": email})`. If the user exists, report this and halt.
4. **Role Validation**: If roles are specified, verify each role exists using `list_documents(doctype="Role", filters={"name": ["in", role_list]})`. Do not assign non-existent roles.
5. **Child Table Format**: Roles must be supplied as a list of dictionaries with the `role` key:
   ```json
   "roles": [
     {"role": "Accounts User"},
     {"role": "Employee"}
   ]
   ```
6. **Always-Ask Confirmation**: User creation involves security and license implications. The tool call requires explicit user confirmation.

## Example Flow

1. User requests: "Add Alex Rivera (alex@accounting.example.org) as an Accounts User."
2. Assistant checks `list_documents(doctype="User", filters={"email": "alex@accounting.example.org"})`.
3. Assistant verifies the role exists via `list_documents(doctype="Role", filters={"name": "Accounts User"})`.
4. Assistant stages `create_document`:
   ```json
   {
     "doctype": "User",
     "data": {
       "email": "alex@accounting.example.org",
       "first_name": "Alex",
       "last_name": "Rivera",
       "send_welcome_email": 1,
       "enabled": 1,
       "roles": [{"role": "Accounts User"}]
     }
   }
   ```
5. Assistant confirms with user: "I will create a new user Alex Rivera with the Accounts User role. A welcome email will be sent for them to set their password."
