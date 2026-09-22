from openultra_browser.local_planner import validate_plan
from openultra_browser.models import BrowserSnapshot, ObservedElement
from openultra_browser.task_progress import planned_search_evidence


def test_literal_route_values_are_accepted_without_navigation_steps():
    result = validate_plan(
        "find flights between Bangkok to Munich on Jan 28",
        '{"requirements":['
        '{"what":"where from","value":"Bangkok"},'
        '{"what":"where to","value":"Munich"},'
        '{"what":"departure","value":"Jan 28"},'
        '{"what":"website","value":"google.com"}]}'
    )
    assert result == {
        "where from": "Bangkok",
        "where to": "Munich",
        "departure": "Jan 28",
    }


def test_compact_form_output_is_supported_but_invented_values_are_rejected():
    result = validate_plan(
        "Submit Aryan, 0637859636, and choose looking to rent",
        '{"requirements":[{"name":"Aryan","phone":"0637859636",'
        '"rent or sell":"rent","email":"made-up@example.com",'
        '"Page":"Sell page"}]}'
    )
    assert result == {"name": "Aryan", "phone": "0637859636", "rent or sell": "rent"}


def test_malformed_output_does_not_supply_inputs():
    assert validate_plan("search for docs", "not JSON") == {}
    assert validate_plan("search for docs", '{"requirements":"query=docs"}') == {}
    assert validate_plan(
        "Contact Brent", '{"requirements":[{"rent or sell":"rent"}]}'
    ) == {}


def test_flight_search_needs_matching_fields_and_visible_results():
    values = {"where from": "Bangkok", "where to": "Munich", "departure": "Jan 28"}
    fields = (
        ObservedElement("origin", "combobox", "Where from?", "input", value="Bangkok"),
        ObservedElement("destination", "combobox", "Where to?", "input", value="Munich"),
        ObservedElement("date", "textbox", "Departure", "input", value="Thu, Jan 28"),
    )
    goal = "find flights between Bangkok to Munich on Jan 28"
    assert not planned_search_evidence(goal, values, BrowserSnapshot("https://example.com", "", "", fields))
    assert planned_search_evidence(
        goal, values,
        BrowserSnapshot("https://example.com", "", "Top departing flights", fields),
    )
    wrong_route = (fields[0], ObservedElement(
        "destination", "combobox", "Where to?", "input", value="Bangkok"
    ), fields[2])
    assert not planned_search_evidence(
        goal, values,
        BrowserSnapshot("https://example.com", "", "Top departing flights", wrong_route),
    )
