from __future__ import annotations

import json
import re
from typing import Any

import httpx
import asyncio

from jobhelper.config import settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    pass


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"Gemini returned no candidates: {payload}")
    parts = candidates[0].get("content", {}).get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("text")]
    if not texts:
        raise LLMError(f"Gemini returned empty text: {payload}")
    return "\n".join(texts).strip()


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMError(f"Expected JSON object, got: {text[:400]}")
    return json.loads(text[start : end + 1])


async def generate(
    prompt: str,
    *,
    system: str | None = None,
    json_mode: bool = False,
    max_retries: int = 3,
) -> str:
    if not settings.gemini_api_key:
        raise LLMError(
            "GEMINI_API_KEY is missing. Create a free key at https://aistudio.google.com/apikey"
        )

    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt}]}]
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 2048,
        },
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    url = GEMINI_URL.format(model=settings.gemini_model)

    delay = 5.0 # Initial retry delay for rate limits
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(max_retries + 1):
            response = await client.post(
                url,
                headers={
                    "x-goog-api-key": settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
        
            if response.status_code in (429, 503) and attempt < max_retries:
                try:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                except asyncio.CancelledError:
                    raise

            if response.status_code >= 400:
                if response.status_code == 429:
                    raise LLMError("Gemini rate limit exceeded (Free tier quota).  Please wait a moment before trying again.")
                if response.status_code == 503:
                    raise LLMError("Gemini servers are experiencing high demand.  Please try again after a few minutes.")
                raise LLMError(f"Gemini HTTP {response.status_code}: {response.text[:800]}")
        return _extract_text(response.json())
    raise LLMError("Gemini request failed after maximum retry attempts")


async def generate_json(prompt: str, *, system: str | None = None) -> dict[str, Any]:
    text = await generate(prompt, system=system, json_mode=True)
    return parse_json_object(text)

async def generate_decision_with_fallback(prompt: str, *, system: str | None = None) -> dict[str, Any]:
    """Tries Gemini first for step decisions; falls back to local Ollama (qwen2.5:7b) if Gemini fails."""
    system_prompt = system or "You are an expert MCP agent controller. Output valid JSON only."
    # 1. Try Gemini first
    try:
        decision = await generate_json(prompt, system=system_prompt)
        if isinstance(decision, dict) and "tool" in decision:
            return decision
    except Exception as e:
        logger.warning(f"[MCP Agent] Gemini step generation failed ({e}). Falling back to local Ollama...")

    # 2. Fallback to Local Ollama (`qwen2.5:7b`)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            payload = {
                "model": "qwen2.5:7b",
                "prompt": prompt + "\n\nCRITICAL INSTRUCTION: Respond ONLY with valid JSON containing keys 'thought', 'tool', and 'args'. No markdown backticks, no extra text.",
                "stream": False,
                "options": {"temperature": 0.1}
            }
            response = await client.post("http://localhost:11434/api/generate", json=payload)
            if response.status_code == 200:
                data = response.json()
                raw_text = data.get("response", "").strip()
                
                # Clean markdown code blocks if the local model wraps them
                if "```json" in raw_text:
                    raw_text = raw_text.split("```json")[1].split("```")[0].strip()
                elif "```" in raw_text:
                    raw_text = raw_text.split("```")[1].split("```")[0].strip()
                
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    logger.info("[MCP Agent] Successfully recovered decision using local Ollama fallback.")
                    return parsed
    except Exception as local_err:
        logger.error(f"[MCP Agent] Local Ollama fallback also failed: {local_err}")

    # 3. Default safe fallback action if all LLMs are down
    return {
        "thought": "LLM providers experiencing high demand. Taking a fresh snapshot to retry.",
        "tool": "browser_snapshot",
        "args": {}
    }
