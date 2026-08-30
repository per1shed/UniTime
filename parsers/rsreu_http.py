from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

RSREU_HTTP_TIMEOUT = httpx.Timeout(connect=60.0, read=120.0, write=30.0, pool=30.0)
RSREU_FETCH_RETRIES = 5
RSREU_HTTP_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)


def create_rsreu_client(proxy: str | None = None) -> httpx.AsyncClient:
    proxy_url = (proxy or os.getenv("RSREU_PROXY") or "").strip() or None
    if proxy_url:
        logger.debug("RSREU HTTP client uses proxy")
    return httpx.AsyncClient(
        timeout=RSREU_HTTP_TIMEOUT,
        follow_redirects=True,
        limits=RSREU_HTTP_LIMITS,
        proxy=proxy_url,
    )


async def fetch_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = RSREU_FETCH_RETRIES,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt < retries - 1:
                await asyncio.sleep(3 * (2**attempt))
    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch {url}")
