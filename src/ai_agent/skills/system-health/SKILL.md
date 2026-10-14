---
name: system-health
description: Inspect Frappe system health including scheduler status, background workers, failed jobs, email queue, and error logs.
risk: read-only
---

<!-- records:
System Health Report:
  - scheduler_status
  - queue_status
  - failing_scheduled_jobs
  - total_background_workers
  - onsite_backups
  - backups_size
  - public_files_size
  - private_files_size
  - pending_emails
  - failed_emails
  - total_errors
Error Log:
  - name
  - method
  - error
  - creation
Email Queue:
  - name
  - status
  - sender
  - recipients
  - creation
-->

# System Health

Read and evaluate site health metrics to deliver an operational summary with concrete next steps.

## Allowed Tools

* `get_document`: Read the `System Health Report` single DocType.
* `list_documents`: Read recent entries from `Error Log` or `Email Queue` if issues are reported.

Do not call write, delete, or shell tools.

## Rules

1. **Read-Only**: This skill only gathers and presents telemetry. Never attempt modifications or job restarts.
2. **Evaluation Hierarchy**: Categorize health status into three states:
   * **OK**: Scheduler is active, queues are healthy, no persistent failed jobs, error rate nominal.
   * **WARN**: Pending emails accumulating, backups older than expected, or intermittent failed background jobs.
   * **FAIL**: Scheduler stopped, workers offline, or recurring fatal error logs.
3. **Investigation Sequence**:
   * Fetch `System Health Report` via `get_document(doctype="System Health Report", name="System Health Report")`.
   * If `failing_scheduled_jobs` is present or `total_errors` is elevated, fetch recent rows from `Error Log` via `list_documents(doctype="Error Log", order_by="creation desc", page_length=5)`.
   * If email issues are indicated, query `Email Queue` via `list_documents(doctype="Email Queue", filters=[["status", "=", "Error"]], page_length=5)`.
4. **Report Structure**:
   * Present findings with a Status table or list:
     - Background Scheduler & Workers
     - Background Job Queues
     - Outgoing Email Delivery
     - File Storage & Backups
     - Error Log Summary
   * Conclude with clear, actionable recommendations for any detected warnings or failures.

## Example Output Structure

| Component | Status | Detail | Next Steps |
| --- | --- | --- | --- |
| Scheduler | OK | Active and executing hourly jobs | None |
| Workers | OK | 2 workers online | None |
| Emails | WARN | 12 pending, 1 failed | Check outgoing SMTP settings in Email Account |
| Error Logs | FAIL | 8 unhandled exceptions in last hour | Inspect Error Log entries for stack trace |
