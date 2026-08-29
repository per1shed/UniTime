from __future__ import annotations

import asyncio

import httpx

RZGMU_HTTP_TIMEOUT = httpx.Timeout(connect=60.0, read=120.0, write=30.0, pool=30.0)
RZGMU_FETCH_RETRIES = 5
RZGMU_HTTP_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)


def create_rzgmu_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=RZGMU_HTTP_TIMEOUT,
        follow_redirects=True,
        limits=RZGMU_HTTP_LIMITS,
    )


async def fetch_response(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = RZGMU_FETCH_RETRIES,
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
