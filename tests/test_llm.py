import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seezar_operator import llm
from seezar_operator.llm import LLMUnavailable, _parse_array, analyse


def test_parses_plain_json():
    assert _parse_array('[{"i":0,"topic":"pricing","models":[]}]') == [
        {"i": 0, "topic": "pricing", "models": []}
    ]


def test_strips_markdown_fences():
    raw = '```json\n[{"i":0,"topic":"service","models":["Ford"]}]\n```'
    assert _parse_array(raw)[0]["topic"] == "service"


def test_extracts_array_from_surrounding_prose():
    raw = 'Sure! Here you go:\n[{"i":1,"topic":"location","models":[]}]\nHope that helps.'
    assert _parse_array(raw)[0]["i"] == 1


def test_salvages_truncated_response():
    """Cut off mid-array - the complete objects must still be recovered."""
    raw = '[{"i":0,"topic":"pricing","models":[]},{"i":1,"topic":"specs","models":[]},{"i":2,"top'
    got = _parse_array(raw)
    assert [g["i"] for g in got] == [0, 1]


def test_unparseable_output_raises():
    with pytest.raises(LLMUnavailable):
        _parse_array("I am afraid I cannot help with that.")


def test_analyse_aligns_results_and_rejects_bad_labels():
    payload = (
        '[{"i":0,"topic":"pricing","models":["Porsche"]},'
        '{"i":1,"topic":"warranty","models":[]},'          # not in the taxonomy
        '{"i":9,"topic":"specs","models":[]}]'             # index outside the batch
    )
    with patch.object(llm, "_post", return_value=payload):
        out = analyse(["a", "b"])

    assert len(out) == 2
    assert out[0]["topic"] == "pricing" and out[0]["models"] == ["Porsche"]
    assert out[1]["topic"] == "other", "an invented label must fall back to 'other'"


def test_a_short_batch_is_retried_and_filled():
    """A reply missing labels must be re-asked, not left on the default."""
    calls = []

    def fake_post(messages):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            return '[{"i":0,"topic":"service","models":[]}]'      # 1 of 3
        return ('[{"i":0,"topic":"location","models":[]},'
                '{"i":1,"topic":"test_drive","models":[]}]')      # the 2 missing

    with patch.object(llm, "_post", side_effect=fake_post):
        out = llm.analyse(["a", "b", "c"])

    assert len(calls) == 2, "the missing messages must be re-asked"
    assert [o["topic"] for o in out] == ["service", "location", "test_drive"]
    assert all(o["topic"] != "other" for o in out), "nothing should fall back to 'other'"


def test_still_unlabelled_after_retry_becomes_other():
    """When the retry also comes back empty, the message is counted as other."""
    calls = []

    def fake_post(messages):
        calls.append(1)
        return '[{"i":0,"topic":"service","models":[]}]' if len(calls) == 1 else "[]"

    with patch.object(llm, "_post", side_effect=fake_post):
        out = llm.analyse(["a", "b"])

    assert len(calls) == 2, "a short reply must still trigger one retry"
    assert out[0]["topic"] == "service"
    assert out[1]["topic"] == "other", "an answer that never arrives is counted as other"


def test_batch_offsets_are_applied():
    """With 2 batches, batch-relative indices must map onto the right messages."""
    original = llm.BATCH_SIZE
    llm.BATCH_SIZE = 2
    payload = ('[{"i":0,"topic":"location","models":[]},'
               '{"i":1,"topic":"service","models":[]}]')
    try:
        with patch.object(llm, "_post", return_value=payload):
            out = llm.analyse(["a", "b", "c", "d"])
    finally:
        llm.BATCH_SIZE = original
    assert [o["topic"] for o in out] == ["location", "service", "location", "service"]
    assert [o["message"] for o in out] == ["a", "b", "c", "d"]
