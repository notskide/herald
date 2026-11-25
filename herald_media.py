import json
import re
import urllib.parse

import aiohttp

MAX_GENERATED_IMAGE_BYTES = 8 * 1024 * 1024


class MediaProviderError(RuntimeError):
    pass


def _provider_error(provider, status, body):
    try:
        parsed = json.loads(body)
        detail = parsed.get("error", {}).get("message") or parsed.get("message") or body
    except (json.JSONDecodeError, AttributeError):
        detail = body
    return MediaProviderError(f"{provider} returned HTTP {status}: {str(detail)[:650]}")


async def generate_image(prompt):
    prompt = str(prompt or "").strip()
    if not prompt:
        raise MediaProviderError("write a prompt for the image first.")
    if len(prompt) > 2048:
        raise MediaProviderError("image prompts must be 2,048 characters or shorter.")
    encoded_prompt = urllib.parse.quote(prompt, safe="")
    endpoint = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed=7"
    timeout = aiohttp.ClientTimeout(total=150, connect=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(endpoint, headers={"User-Agent": "HeraldDiscordBot/1.0"}) as response:
            if response.status != 200:
                body = await response.text()
                raise _provider_error("Pollinations", response.status, body)
            image_bytes = await response.read()
            content_type = response.headers.get("Content-Type", "").lower()
            if not content_type.startswith("image/"):
                raise MediaProviderError("Pollinations did not return an image.")
    if not image_bytes:
        raise MediaProviderError("Pollinations returned an empty image.")
    if len(image_bytes) > MAX_GENERATED_IMAGE_BYTES:
        raise MediaProviderError("the generated image is too large to send through Discord.")
    return image_bytes
