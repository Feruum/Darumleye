"""Review modes accepted by the CLI, API, durable workers and report archive."""

from typing import Literal, get_args

MultiAgentReviewMode = Literal["multi_agent", "multi_agent_direct"]
ReviewMode = Literal["legacy", MultiAgentReviewMode]

MULTI_AGENT_MODES = get_args(MultiAgentReviewMode)
REVIEW_MODES = get_args(ReviewMode)
REVIEW_MODE_LABELS = {
    "legacy": "Legacy",
    "multi_agent": "мультиагентное ревью (парная валидация)",
    "multi_agent_direct": "мультиагентное ревью (прямая валидация)",
}
