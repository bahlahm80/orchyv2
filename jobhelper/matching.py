from __future__ import annotations

import re
from dataclasses import dataclass

from jobhelper.llm import LLMError, generate_json

STOPWORDS = {
    "and",
    "the",
    "with",
    "for",
    "from",
    "that",
    "this",
    "your",
    "you",
    "our",
    "are",
    "was",
    "will",
    "role",
    "job",
    "team",
}


@dataclass
class MatchResult:
    score: float
    matched: bool
    reason: str


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z+#.]{1,}", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def heuristic_score(resume: str, title: str, description: str) -> MatchResult:
    resume_tokens = _tokens(resume)
    job_tokens = _tokens(f"{title} {description}")
    if not job_tokens:
        return MatchResult(0.0, False, "Job posting has no usable text.")
    overlap = resume_tokens & job_tokens
    coverage = len(overlap) / max(len(job_tokens), 1)
    skill_hits = len(overlap) / max(len(resume_tokens), 1)
    score = min(1.0, (coverage * 0.45) + (skill_hits * 0.55) + (0.12 if "engineer" in title.lower() else 0))
    reason = (
        f"Heuristic overlap {len(overlap)} tokens. "
        f"Title/skill coverage={coverage:.2f}, resume hit-rate={skill_hits:.2f}."
    )
    return MatchResult(score, False, reason)


async def score_job(
    resume: str,
    *,
    title: str,
    company: str,
    location: str,
    description: str,
    threshold: float,
) -> MatchResult:
    baseline = heuristic_score(resume, title, description)
    prompt = f"""Score whether this job is a genuine match for the candidate.

Return JSON:
{{
  "score": 0.0-1.0,
  "matched": true/false,
  "reason": "short justification",
  "risks": ["optional list of mismatches"]
}}

Guardrails:
- matched must be false if the role is sales-only, unpaid, crypto trading, or unrelated to the resume.
- Prefer software/backend/full-stack/AI engineering roles in the US or remote.
- Do not inflate scores for generic buzzwords.

CANDIDATE RESUME:
{resume}

JOB:
Title: {title}
Company: {company}
Location: {location}
Description: {description[:4000]}
"""
    try:
        data = await generate_json(
            prompt,
            system="You are a strict job-matching reviewer. Be conservative.",
        )
        score = float(data.get("score", baseline.score))
        score = max(0.0, min(1.0, score))
        risks = data.get("risks") or []
        reason = str(data.get("reason") or baseline.reason)
        if risks:
            reason = reason + " Risks: " + "; ".join(map(str, risks))
        matched = bool(data.get("matched", score >= threshold)) and score >= threshold
        return MatchResult(score=score, matched=matched, reason=reason)
    except (LLMError, ValueError, TypeError) as exc:
        matched = baseline.score >= threshold
        return MatchResult(
            score=baseline.score,
            matched=matched,
            reason=f"{baseline.reason} LLM fallback ({exc}).",
        )
