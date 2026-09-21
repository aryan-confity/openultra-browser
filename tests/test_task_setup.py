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
