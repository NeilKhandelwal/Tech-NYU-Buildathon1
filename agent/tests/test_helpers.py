"""The pure helpers the transcript, case data and the one-problem-per-case rule depend on."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agent import can_open_case, clean_text, digits, is_filed, normalize_code, valid_phone


def test_clean_text_strips_expressive_tags():
    """Expressive TTS embeds <expr/> tags; staff must never see them in the transcript."""
    raw = '<expr type="expression" label="calm"/> Hello,  <expr label="happy"/>how can I\nhelp?'
    assert clean_text(raw) == "Hello, how can I help?"


def test_clean_text_plain_passthrough():
    """Text without tags is untouched, so a future TTS without tags still works."""
    assert clean_text("Case C-1001 is open.") == "Case C-1001 is open."


def test_digits_normalizes_spoken_phone():
    """Callers say numbers with dots, dashes and spaces; lookup must match what create stored."""
    assert digits("925.915-7062") == "9259157062"
    assert digits("(555) 123 4567") == "5551234567"


def test_valid_phone_rejects_partial_numbers():
    """STT split "25" + "7062" into a 6-digit phone once; a case filed under it can never be looked up."""
    assert valid_phone("925.915-7062")
    assert not valid_phone("257062")
    assert not valid_phone("1 925 915 7062 3")


def test_normalize_code_accepts_spoken_forms():
    """The code is the only key to a case: a caller who says it right must never be turned away
    over punctuation or the filler words people put between spoken words."""
    assert normalize_code("Blue River, Maple") == "blue-river-maple"
    assert normalize_code("blue and river dash maple") == "blue-river-maple"
    assert normalize_code(" BLUE  river   maple ") == "blue-river-maple"
    assert normalize_code("blue-river-maple") == "blue-river-maple"


def test_can_open_case_allows_the_first_case():
    """Nothing to finish yet, so the first create_case of a call is always allowed."""
    assert can_open_case(None, False, False)


def test_can_open_case_blocks_a_second_case_until_the_first_is_filed():
    """The LLM re-calls create_case when a caller keeps talking; a second case opened before the
    first has an issue type AND a description leaves staff with a half-filled case."""
    assert not can_open_case("C-1001", False, False)
    assert not can_open_case("C-1001", True, False)
    assert can_open_case("C-1001", True, True)


def test_is_filed_makes_a_finished_case_read_only():
    """Seen live: "my trash wasn't collected" after a pothole was filed was written onto the
    pothole case via update_case. Once filed, a case must only ever be appended to, never rewritten."""
    assert not is_filed(True, False)
    assert is_filed(True, True)
