from openultra_browser.task_setup import GOOGLE_START_URL, plan_task


def test_search_task_extracts_literal_query_and_starts_at_google():
    setup = plan_task(
        "Search Google for WDX Properties, open the official website, then open Rent"
    )

    assert setup.start_url == GOOGLE_START_URL
    assert setup.prepared_inputs == {"search query": "WDX Properties"}


def test_bare_domain_task_starts_on_that_site_without_inventing_text():
    setup = plan_task("Go to wdxproperties.com, open Rent, and open the first listing")

    assert setup.start_url == "https://wdxproperties.com"
    assert setup.prepared_inputs == {}


def test_explicit_url_is_preserved():
    setup = plan_task("Open https://example.com/docs?view=all and read the first section")

    assert setup.start_url == "https://example.com/docs?view=all"
    assert setup.prepared_inputs == {}


def test_generic_task_uses_the_task_as_search_text():
    setup = plan_task("Find the official documentation for Python dataclasses")

    assert setup.start_url == GOOGLE_START_URL
    assert setup.prepared_inputs == {
        "search query": "Find the official documentation for Python dataclasses"
    }


def test_explicit_form_details_are_bound_without_inventing_missing_values():
    setup = plan_task(
        "Go to wdxproperties.com, fill the form and submit Aryan, 0637859636, "
        "same as WhatsApp, and aryan line ID looking to rent"
    )

    assert setup.prepared_inputs == {
        "name": "Aryan",
        "phone": "0637859636",
        "line id": "aryan",
        "whatsapp number": "0637859636",
        "rent or sell": "Rent",
    }


def test_form_parser_leaves_unprovided_optional_details_absent():
    setup = plan_task(
        "Go to wdxproperties.com, fill this form and submit Aryan, 0637859636, "
        "and choose looking to rent"
    )

    assert setup.prepared_inputs == {
        "name": "Aryan",
        "phone": "0637859636",
        "rent or sell": "Rent",
    }
