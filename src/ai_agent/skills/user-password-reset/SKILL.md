---
name: user-password-reset
description: Explain the Frappe password reset procedure, handle credential disclosures safely, and clarify assistant limitations.
risk: read-only
---

<!-- records:
User:
  - name
  - email
  - enabled
-->

# User Password Reset

Guide users through password reset flows while maintaining credential confidentiality.

## Allowed Tools

* `list_documents`: Verify if a user account exists and is enabled, without accessing secret hashes.

Do not call write, update, or delete tools.

## Rules

1. **Zero Credential Exposure**: Never ask for, accept, or output passwords, PINs, or API secrets.
2. **Immediate Warning on Disclosure**: If the user pastes a password or secret into the chat:
   * Issue an immediate security alert warning them that chat messages are stored in database records and transmitted to AI model providers.
   * Urge them to change or invalidate that password immediately.
3. **State Limitations Plainly**:
   * Explicitly inform the user that the AI assistant cannot set passwords or trigger password reset emails directly.
   * Never pretend or claim that a reset email has been dispatched.
4. **Legitimate Reset Methods**: Explain the two standard Frappe reset mechanisms:
   * **Self-Service**: Direct the user to the login screen and click **Forgot Password?**, entering their registered email address.
   * **Administrator Reset**: A user with the **System Manager** role can open the `User` document in Desk and click **Actions > Reset Password**.
5. **Account Status Check**: If requested, call `list_documents(doctype="User", filters=[["email", "=", email]], fields=["name", "email", "enabled"])` to confirm whether the account is active or disabled.

## Example Response

```text
The AI assistant cannot set passwords or send password reset emails directly.

To reset your password in Frappe / ERPNext:
1. Navigate to the login page: /login
2. Click "Forgot Password?" below the sign-in form.
3. Enter your registered email address and submit. A secure reset link will be sent to your inbox.

If your organization has disabled self-service resets, ask a System Manager to open your User record in Desk and select "Reset Password".

Security Reminder: Never enter personal passwords or secret keys in this chat window.
```
