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


def test_analyse_defaults_messages_a_batch_omitted():
    """A short reply must not shift labels onto the wrong messages."""
    with patch.object(llm, "_post", return_value='[{"i":2,"topic":"service","models":[]}]'):
        out = analyse(["m0", "m1", "m2"])
    assert [o["topic"] for o in out] == ["other", "other", "service"]
    assert [o["message"] for o in out] == ["m0", "m1", "m2"]


def test_batch_offsets_are_applied():
    """With 2 batches, batch-relative index 0 must map to the right global message."""
    original = llm.BATCH_SIZE
    llm.BATCH_SIZE = 2
    try:
        with patch.object(llm, "_post", return_value='[{"i":0,"topic":"location","models":[]}]'):
            out = analyse(["a", "b", "c", "d"])
    finally:
        llm.BATCH_SIZE = original
    assert out[0]["topic"] == "location"
    assert out[2]["topic"] == "location"
    assert out[1]["topic"] == "other" and out[3]["topic"] == "other"
