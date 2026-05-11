from __future__ import annotations

import asyncio
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from plant_monitor.models import PlantConfig, PlantStatus, ServiceConfig

LOGGER = logging.getLogger(__name__)
RESPONSES_URL = "https://api.openai.com/v1/responses"


async def rewrite_notification_text(
    config: ServiceConfig,
    plant: PlantConfig,
    status: PlantStatus,
    fallback: str,
) -> str:
    if not config.llm_notification_text or not config.openai_api_key:
        return fallback
    try:
        return await asyncio.to_thread(_rewrite_sync, config, plant, status, fallback)
    except Exception as exc:
        LOGGER.warning("LLM notification text failed; using deterministic text: %s", exc)
        return fallback


def _rewrite_sync(
    config: ServiceConfig,
    plant: PlantConfig,
    status: PlantStatus,
    fallback: str,
) -> str:
    prompt = (
        "Rewrite this plant alert as concise phone notification text. "
        "Do not change facts, severity, instructions, or watering eligibility. "
        "Use plain text, 1-4 short bullet lines, no emoji.\n\n"
        f"Plant: {plant.location} {plant.name}\n"
        f"Severity: {status.label.label}\n"
        f"Deterministic text:\n{fallback}"
    )
    payload = {
        "model": config.openai_model,
        "input": prompt,
        "max_output_tokens": 120,
    }
    request = Request(
        RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"OpenAI returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach OpenAI: {exc.reason}") from exc

    text = _extract_output_text(body).strip()
    return text or fallback


def _extract_output_text(body: dict) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts: list[str] = []
    for item in body.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)
