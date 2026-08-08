from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Mapping
from typing import Any

import aiohttp


class HttpFailure(RuntimeError):
    pass


class AsyncJsonClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        concurrency: int = 4,
        attempts: int = 7,
        minimum_interval: float = 0.0,
    ) -> None:
        self.session = session
        self.semaphore = asyncio.Semaphore(concurrency)
        self.attempts = attempts
        self.minimum_interval = minimum_interval
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    async def _pace(self) -> None:
        if self.minimum_interval <= 0:
            return
        async with self._rate_lock:
            wait = self.minimum_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    @staticmethod
    def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 120.0)
            except ValueError:
                parsed = email.utils.parsedate_to_datetime(retry_after)
                return min(max(parsed.timestamp() - time.time(), 0.0), 120.0)
        if headers.get("X-RateLimit-Remaining") == "0":
            reset = headers.get("X-RateLimit-Reset")
            if reset:
                return min(max(float(reset) - time.time(), 0.0) + 1.0, 120.0)
        return min(2**attempt + random.random(), 120.0)

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> tuple[Any, Mapping[str, str]]:
        last_error = "request was not attempted"
        for attempt in range(self.attempts):
            await self._pace()
            try:
                async with self.semaphore:
                    async with self.session.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json_body,
                    ) as response:
                        if response.status < 400:
                            return await response.json(content_type=None), response.headers
                        body = (await response.text())[:2000]
                        last_error = f"HTTP {response.status}: {body}"
                        if response.status not in {403, 408, 409, 425, 429, 500, 502, 503, 504}:
                            raise HttpFailure(last_error)
                        delay = self._retry_delay(response.headers, attempt)
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = repr(error)
                delay = min(2**attempt + random.random(), 120.0)
            if attempt + 1 < self.attempts:
                await asyncio.sleep(delay)
        raise HttpFailure(f"request failed after {self.attempts} attempts: {last_error}")

    async def request_bytes(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        maximum_bytes: int = 100_000_000,
    ) -> tuple[bytes, Mapping[str, str]]:
        last_error = "request was not attempted"
        for attempt in range(self.attempts):
            await self._pace()
            try:
                async with self.semaphore:
                    async with self.session.request(method, url, headers=headers) as response:
                        if response.status < 400:
                            content_length = response.headers.get("Content-Length")
                            if content_length and int(content_length) > maximum_bytes:
                                raise HttpFailure(f"response exceeds {maximum_bytes} bytes")
                            payload = await response.read()
                            if len(payload) > maximum_bytes:
                                raise HttpFailure(f"response exceeds {maximum_bytes} bytes")
                            return payload, response.headers
                        body = (await response.text())[:2000]
                        last_error = f"HTTP {response.status}: {body}"
                        if response.status not in {403, 408, 409, 425, 429, 500, 502, 503, 504}:
                            raise HttpFailure(last_error)
                        delay = self._retry_delay(response.headers, attempt)
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = repr(error)
                delay = min(2**attempt + random.random(), 120.0)
            if attempt + 1 < self.attempts:
                await asyncio.sleep(delay)
        raise HttpFailure(f"request failed after {self.attempts} attempts: {last_error}")


def dotted_get(value: Any, path: str | None, default: Any = None) -> Any:
    if not path:
        return default
    current = value
    for component in path.split("."):
        if isinstance(current, Mapping) and component in current:
            current = current[component]
        else:
            return default
    return current


def dotted_set(value: dict[str, Any], path: str, new_value: Any) -> None:
    components = path.split(".")
    current = value
    for component in components[:-1]:
        child = current.setdefault(component, {})
        if not isinstance(child, dict):
            raise ValueError(f"cannot set {path!r}: {component!r} is not an object")
        current = child
    current[components[-1]] = new_value
