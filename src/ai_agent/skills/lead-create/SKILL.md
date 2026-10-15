---
name: lead-create
description: Create a complete lead, the first record of the sales workflow, researching the organisation on the web to fill it.
risk: writes
governs: create_document:Lead, create_documents:Lead, create_document:CRM Lead, create_documents:CRM Lead
---

<!-- records:
Lead:
  - company_name
  - website
  - industry
  - country
  - notes
CRM Note:
  - note
CRM Lead:
  - organization
  - website
  - industry
  - territory
FCRM Note:
  - title
  - content
  - reference_doctype
  - reference_docname
-->

# Lead Create

A lead starts the sales workflow: every later step (qualifying it, assigning it to a territory or
a salesperson, the opportunity, the quotation) works from what the lead holds. A lead with only a
name sends the user back to research it by hand, so research it now and fill it completely.

The lead DocType differs between Frappe apps (`Lead` in ERPNext, `CRM Lead` in Frappe CRM). Learn
its fields from its definition; use only fieldnames it has.

## Steps

1. **Duplicates first.** Look for an existing lead with `list_documents`, filtering on the
   organisation name, its website or the person's email. If one exists, report it, or update it
   with what is new; never create a second.
2. **Research the organisation.** For an organisation the request names, `web_search` for its
   official site, then `fetch_web_page` on that site (and its about or contact page when the home
   page is thin). Do this even when the request gives only a name.
3. **Fill every field the research and the request support:**
   * the organisation's legal name, and the person's first and last name when one is named;
   * its own website (never a directory or search result), and email and phone only as read on a
     page or given by the user;
   * industry, country, city and state, number of employees, and annual revenue when a page
     states them;
   * source and request type from how the lead came in.
4. **Link fields take existing records.** Find the country, industry, territory or source record
   that matches with `list_documents`, filtered on the value (`{"name": ["like", "%India%"]}`).
   Never take the first row of an unfiltered list. If no record matches, leave the field empty
   and say so.
5. **Write a note when the request carries one.** What the user says about the lead that fits no
   field (why they came, "might want our implementation services", what was discussed, a
   follow-up) or a note the user asks for goes in a note linked to the lead, never in another
   field. The note is the user's words, plainly; do not repeat the research already in fields.
   * `Lead` (ERPNext): the note is a row of the lead's `notes` table (CRM Note). Creating the
     lead, put it in the same call: `"data": {..., "notes": [{"note": "<p>...</p>"}]}`. For a
     lead that exists, read it first and send its current `notes` rows, each with its `name`,
     followed by the new one: `update_document` replaces the whole table.
   * `CRM Lead` (Frappe CRM): after the lead is created, `create_document` an `FCRM Note` with
     `"data": {"title": "...", "content": "<p>...</p>", "reference_doctype": "CRM Lead",
     "reference_docname": "<the lead's name>"}`, the name the lead's create returned.
   * Never the desk `Note` DocType: it has no link to the lead and does not show on it.
6. **Leave out what you could not verify** and say what is missing in one line after the change,
   so the user knows what to complete.
