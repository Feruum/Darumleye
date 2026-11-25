"""Review-mode identities used by admissions, durable workers and archived reports."""

from typing import Literal, get_args

type ActiveReviewMode = Literal["multi_agent", "multi_agent_direct"]
type ReviewMode = Literal["legacy", "multi_agent", "multi_agent_direct"]

ACTIVE_REVIEW_MODES = get_args(ActiveReviewMode.__value__)
REVIEW_MODES = get_args(ReviewMode.__value__)


def new_review_mode(value: str) -> ActiveReviewMode:
    """Resolve the retired request alias before persisting a new run's identity."""
    if value in ("legacy", "multi_agent_direct"):
        return "multi_agent_direct"
    if value == "multi_agent":
        return "multi_agent"
    raise ValueError("Unknown review mode")
