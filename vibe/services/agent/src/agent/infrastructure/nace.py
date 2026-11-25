"""Shared response validation for the Nace System One API."""

from collections.abc import Collection

NACE_SYSTEM_ONE_URL = "https://console.nace.ai/v1/systemone"


def parse_noul_probabilities(
    body: object, expected_ids: Collection[str]
) -> dict[str, float]:
    """Require one finite probability per named question, with no missing or extra IDs."""
    if not isinstance(body, dict):
        raise ValueError("Invalid decision payload")
    answers = body.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(expected_ids):
        raise ValueError("Missing or unexpected decision answers")
    scores = {}
    for question_id in expected_ids:
        answer = answers[question_id]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise ValueError("Invalid decision answer type")
        score = answer.get("noul")
        if type(score) not in (float, int) or not 0 <= score <= 1:
            raise ValueError("Decision probability out of range")
        scores[question_id] = float(score)
    return scores
