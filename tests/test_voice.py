from openultra_browser.voice import (
    prepare_voice_candidate,
    should_commit_voice_candidate,
)


def test_first_reversible_atomic_voice_clause_can_commit_early():
    candidate = prepare_voice_candidate("Open wikipedia and search for Alan Turing")

    assert candidate.text == "Open wikipedia"
    assert candidate.eligible is True
    assert should_commit_voice_candidate(
        candidate,
        command_kind="reversible_closed_set",
        confidence=0.82,
        completeness=0.88,
    )


def test_payload_and_side_effect_voice_clauses_wait_for_final_speech():
    for transcript in (
        "search for Alan",
        "type Aryan into the name field",
        "change the date to September",
        "submit the form",
        "dislike this video",
    ):
        candidate = prepare_voice_candidate(transcript)
        assert candidate.eligible is False


def test_incomplete_or_model_uncertain_voice_command_does_not_commit():
    incomplete = prepare_voice_candidate("click on the")
    candidate = prepare_voice_candidate("click Rent")

    assert incomplete.eligible is False
    assert not should_commit_voice_candidate(
        candidate,
        command_kind="reversible_closed_set",
        confidence=0.64,
        completeness=0.9,
    )
    assert not should_commit_voice_candidate(
        candidate,
        command_kind="reversible_closed_set",
        confidence=0.9,
        completeness=0.69,
    )
    assert not should_commit_voice_candidate(
        candidate,
        command_kind="free_text_payload",
        confidence=0.9,
        completeness=0.9,
    )
