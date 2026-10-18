"""
An empty answer is no extracted field (final audit 18.10.).

run_outcome counted [] (and [None], "") as extracted: a run whose only answers
were empty lists next to failed fields was reported "partial" instead of
"failed" - and a client would apply that nothing over existing metadata.
"""

from src.services.metadata_generation import run_outcome


def _extraction(values, errors):
    return {
        "values": values,
        "errors": errors,
        "first_error": None,
        "fatal_error": None,
        "skipped": 0,
    }


def test_empty_answers_are_not_counted_as_extracted():
    values = {"ccm:taxonid": [], "cclom:general_keyword": [None], "x": ""}
    outcome = run_outcome(_extraction(values, ["cclom:title: timeout"]), values)

    assert outcome["fields_extracted"] == 0
    assert outcome["status"] == "failed"


def test_real_values_are_still_counted():
    values = {"cclom:title": "Titel", "ccm:taxonid": [], "n": 0, "b": False}
    outcome = run_outcome(_extraction(values, ["x: timeout"]), values)

    assert outcome["fields_extracted"] == 3
    assert outcome["status"] == "partial"
