"""Safety envelope for committing a partial spoken browser command."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .task_progress import split_task_steps

PAYLOAD_PREFIX = re.compile(
    r"^\s*(?:search|find|look\s+up|google|type|enter|fill|write|select|choose|"
    r"change|set)\b",
    re.IGNORECASE,
)
SIDE_EFFECT_PREFIX = re.compile(
    r"^\s*(?:submit|send|share|upload|download|delete|remove|buy|purchase|pay|"
    r"order|book|confirm|sign\s+in|log\s+in|like|dislike|follow|unfollow)\b",
    re.IGNORECASE,
)
REVERSIBLE_PREFIX = re.compile(
    r"^\s*(?:go\s+(?:to|back|forward)|open|visit|navigate|click|press|scroll|"
    r"reload|refresh|play|pause)\b",
    re.IGNORECASE,
)
DANGLING_END = re.compile(
    r"\b(?:a|an|and|at|for|from|in|into|of|on|the|then|to|with)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VoiceCandidate:
    text: str
    eligible: bool
    reason: str


def prepare_voice_candidate(transcript: str) -> VoiceCandidate:
    """Return the first atomic clause and a deterministic early-action guard."""
    clean = " ".join(transcript.split()).strip(" ,.;")
    if not clean:
        return VoiceCandidate("", False, "No spoken command is available yet")
    candidate = split_task_steps(clean)[0]
    if len(candidate.split()) < 2 or DANGLING_END.search(candidate):
        return VoiceCandidate(candidate, False, "The spoken command is still incomplete")
    if PAYLOAD_PREFIX.search(candidate):
        return VoiceCandidate(candidate, False, "Free text must not be truncated")
    if SIDE_EFFECT_PREFIX.search(candidate):
        return VoiceCandidate(candidate, False, "Side effects require a final utterance")
    if not REVERSIBLE_PREFIX.search(candidate):
        return VoiceCandidate(candidate, False, "The command is not an early reversible action")
    return VoiceCandidate(candidate, True, "Reversible atomic command")


def should_commit_voice_candidate(
    candidate: VoiceCandidate,
    *,
    command_kind: str,
    confidence: float,
    completeness: float,
) -> bool:
    """Require deterministic and model agreement before acting on partial speech."""
    return bool(
        candidate.eligible
        and command_kind == "reversible_closed_set"
        and confidence >= 0.65
        and completeness >= 0.7
    )
