"""A DocType definition becomes a field list the prompt can hold; everything else passes through."""

from __future__ import annotations

import json

from ai_agent.agent.condense import (
    check_write_data,
    condense_doctype,
    condense_tool_result,
    unsupported_values,
)

_LEAD = {
    "name": "Lead",
    "description": "Someone who might buy from you.",
    "autoname": "naming_series:",
    "title_field": "lead_name",
    "fields": [
        {
            "fieldname": "naming_series",
            "label": "Series",
            "fieldtype": "Select",
            "options": "CRM-LEAD-.YYYY.-",
            "hidden": 0,
        },
        {"fieldname": "sb_details", "fieldtype": "Section Break", "label": "Details"},
        {"fieldname": "first_name", "label": "First Name", "fieldtype": "Data"},
        {"fieldname": "lead_name", "label": "Full Name", "fieldtype": "Data", "read_only": 0},
        {"fieldname": "company_name", "label": "Organization Name", "fieldtype": "Data"},
        {"fieldname": "website", "label": "Website", "fieldtype": "Data"},
        {"fieldname": "country", "label": "Country", "fieldtype": "Link", "options": "Country"},
        {
            "fieldname": "status",
            "label": "Status",
            "fieldtype": "Select",
            "options": (
                "Lead\nOpen\nReplied\nOpportunity\nQuotation\nLost Quotation\nInterested\n"
                "Converted\nDo Not Contact"
            ),
            "reqd": 1,
            "default": "Lead",
        },
        {
            "fieldname": "email_id",
            "label": "Email",
            "fieldtype": "Data",
            "options": "Email",
            "unique": 1,
        },
        {"fieldname": "notes", "label": "Notes", "fieldtype": "Table", "options": "CRM Note"},
        {"fieldname": "secret", "label": "Secret", "fieldtype": "Data", "hidden": 1},
        {"fieldname": "cb", "fieldtype": "Column Break"},
    ],
}


def _raw() -> str:
    return "Retrieved DocType document: Lead\n" + json.dumps(_LEAD)


class TestCondenseDoctype:
    def test_one_line_per_data_field_with_flags_and_options(self):
        out = condense_doctype(_raw())
        assert out.startswith(
            "DocType Lead: Someone who might buy from you.\n"
            "naming: naming_series:; title field: lead_name\n"
        )
        assert "- company_name | Organization Name | Data |" in out
        assert "- country | Country | Link | | Country" in out
        assert (
            "- status | Status | Select | required, fills itself | Lead, Open, Replied, "
            "Opportunity, Quotation, Lost Quotation, Interested, Converted, … (9 values); "
            "default Lead"
        ) in out
        assert "- email_id | Email | Data | unique" in out
        assert "- notes | Notes | Table | | CRM Note" in out
        # status is required but has a default, so a new Lead needs nothing more
        assert out.endswith("must fill: none")

    def test_layout_and_hidden_fields_are_dropped(self):
        out = condense_doctype(_raw())
        assert "sb_details" not in out and "Column Break" not in out and "secret" not in out

    def test_it_is_far_smaller_than_the_raw_document(self):
        assert len(condense_doctype(_raw())) < len(_raw()) / 2

    def test_anything_else_passes_through(self):
        assert condense_doctype("not json at all") == "not json at all"
        assert condense_doctype('prefix {"name": "X"}') == 'prefix {"name": "X"}'  # no fields
        assert condense_doctype("prefix {broken") == "prefix {broken"


class TestCondenseToolResult:
    def test_only_get_document_on_doctype_is_condensed(self):
        raw = _raw()
        assert (
            condense_tool_result("get_document", {"doctype": "DocType", "name": "Lead"}, raw) != raw
        )
        assert condense_tool_result("get_document", {"doctype": "Lead", "name": "L-1"}, raw) == raw
        assert condense_tool_result("list_documents", {"doctype": "DocType"}, raw) == raw


class TestCheckWriteData:
    def test_known_fields_pass(self):
        data = {"company_name": "Acme", "website": "https://acme.test", "country": "India"}
        assert check_write_data(_raw(), "Lead", data) is None

    def test_unknown_field_is_named_with_the_real_ones(self):
        out = check_write_data(_raw(), "Lead", {"organization": "Acme", "website": "x"})
        assert out is not None
        assert "no field named 'organization'" in out
        assert "company_name" in out and "sb_details" not in out

    def test_table_field_needs_rows(self):
        out = check_write_data(_raw(), "Lead", {"lead_name": "A", "notes": "Gold Partner"})
        assert out is not None and "notes is a table field" in out and "list of row objects" in out
        assert check_write_data(_raw(), "Lead", {"notes": [{"note": "x"}]}) is None

    def test_standard_fields_are_always_allowed(self):
        assert check_write_data(_raw(), "Lead", {"name": "L-1", "docstatus": 0}) is None

    def test_an_unreadable_definition_checks_nothing(self):
        assert check_write_data("Tool call failed: nope", "Lead", {"x": 1}) is None
        assert check_write_data(_raw(), "Lead", "not a dict") is None


class TestUnsupportedValues:
    _SEEN = (
        "Examplia Systems (https://directory.test/p/examplia)\n"
        "View website (https://www.examplia-systems.test)\nmail sales@acme.test"
    )

    def test_an_address_that_was_read_passes_however_it_is_written(self):
        for url in (
            "https://www.examplia-systems.test",
            "https://examplia-systems.test/",
            "http://EXAMPLIA-SYSTEMS.test",
        ):
            assert unsupported_values({"website": url}, self._SEEN) == []

    def test_an_address_that_was_never_read_is_flagged(self):
        out = unsupported_values(
            {"website": "https://examplia.test", "company_name": "Examplia"}, self._SEEN
        )
        assert out == [("website", "https://examplia.test")]

    def test_a_near_miss_is_not_a_match(self):
        # examplia.test is a prefix of nothing here, but examplia-systems.test contains "examplia"
        assert (
            unsupported_values(
                {"website": "https://examplia.test"}, "https://examplia-systems.test"
            )
            != []
        )

    def test_emails_are_checked_too(self):
        assert unsupported_values({"email_id": "sales@acme.test"}, self._SEEN) == []
        assert unsupported_values({"email_id": "info@acme.test"}, self._SEEN) == [
            ("email_id", "info@acme.test")
        ]

    def test_plain_text_and_non_strings_are_never_flagged(self):
        data = {
            "lead_name": "Acme",
            "country": "India",
            "notes": [{"note": "http://x.test"}],
            "n": 3,
        }
        assert unsupported_values(data, "") == []
        assert unsupported_values("not a dict", "") == []
