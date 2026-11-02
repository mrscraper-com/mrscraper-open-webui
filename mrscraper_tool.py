"""
title: MrScraper
author: MrScraper
author_url: https://mrscraper.com
git_url: https://github.com/mrscraper-com/mrscraper-open-webui
description: Retrieve public web pages as Markdown or HTML, search the web, and check MrScraper account usage.
required_open_webui_version: 0.6.0
requirements: mrscraper-sdk>=0.6.0
version: 0.2.0
licence: MIT
"""

import json
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from mrscraper import MrScraper
from mrscraper.exceptions import AuthenticationError, MrScraperError, NetworkError
from pydantic import BaseModel, Field

EventEmitter = Optional[Callable[[dict], Awaitable[None]]]

# MrScraper's web search runs as the "websearch" action on the Scraper API host.
WEB_SEARCH_ENDPOINT = "https://api.mrscraper.com"


async def _status(emitter: EventEmitter, description: str, done: bool) -> None:
    if emitter:
        await emitter(
            {"type": "status", "data": {"description": description, "done": done}}
        )


def _failure(action: str, error: MrScraperError) -> str:
    """Describes a failed call without the SDK's message, which can carry request URLs."""
    if isinstance(error, AuthenticationError):
        return f"MrScraper could not {action}: the API key in this tool's Valves is missing or invalid."
    if isinstance(error, NetworkError):
        return f"MrScraper could not {action}: the MrScraper API could not be reached."
    status = getattr(error, "status_code", None)
    return f"MrScraper could not {action}" + (f" (HTTP {status})." if status else ".")


def _search_query(query: str) -> str:
    """Returns the query text, or the q parameter of a search results URL."""
    value = query.strip()
    if value.lower().startswith(("http://", "https://")):
        return (parse_qs(urlparse(value).query).get("q") or [value])[0].strip()
    return value


class Tools:
    class Valves(BaseModel):
        api_key: str = Field(
            default="",
            description="MrScraper API key from https://app.mrscraper.com/api-tokens",
        )
        max_characters: int = Field(
            default=100_000,
            description="Longest page text returned to the model; 0 returns the whole page.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _client(self) -> MrScraper:
        if not self.valves.api_key.strip():
            raise AuthenticationError("MrScraper API key is not set.")
        return MrScraper(token=self.valves.api_key.strip())

    async def scrape_page(
        self,
        url: str,
        output_format: str = "markdown",
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """
        Retrieve a public web page through MrScraper and return its content. MrScraper renders JavaScript-heavy pages in a browser when needed. Each call uses MrScraper plan tokens.
        :param url: Absolute http or https URL of the page.
        :param output_format: "markdown" (default, best for reading) or "html" (when exact markup matters).
        """
        output_format = "html" if output_format.strip().lower() == "html" else "markdown"
        await _status(__event_emitter__, f"Retrieving {url}", False)
        try:
            # Markdown is always requested: for HTML-only requests the API returns
            # the raw page string, which mrscraper-sdk 0.6.0 cannot parse.
            response = await self._client().scraper_api(
                url, html=output_format == "html", markdown=True
            )
        except MrScraperError as error:
            await _status(__event_emitter__, "MrScraper request failed", True)
            return _failure(f"retrieve {url}", error)
        data = response.get("data") or {}
        content = data.get(output_format) or ""
        if not content:
            await _status(__event_emitter__, "No content returned", True)
            return f"MrScraper returned no {output_format} for {url}."
        limit = self.valves.max_characters
        truncated = limit > 0 and len(content) > limit
        if truncated:
            content = content[:limit]
        await _status(__event_emitter__, f"Retrieved {url}", True)
        note = (
            f"\n\n[Truncated to {limit} characters; raise max_characters in the tool's Valves to get more.]"
            if truncated
            else ""
        )
        return f"Source: {url}\n\n{content}{note}"

    async def web_search(
        self,
        query: str,
        region: str = "",
        num_results: int = 10,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """
        Search the web through MrScraper and return ranked results as JSON (rank, title, link, description, source). Use it to find pages when no URL is known, then read chosen pages with scrape_page. Each call uses MrScraper plan tokens.
        :param query: Search terms, or a search results URL whose q parameter is used.
        :param region: Optional two-letter country code for the results, such as us, gb, or id. Defaults to us.
        :param num_results: Number of results, from 10 to 100. Defaults to 10.
        """
        searched = _search_query(query)
        action = f"search for {searched!r}"
        api_key = self.valves.api_key.strip()
        if not api_key:
            return _failure(action, AuthenticationError("MrScraper API key is not set."))
        body: dict[str, Any] = {
            "action": "websearch",
            "query": searched,
            "numResults": min(100, max(10, num_results)),
        }
        if region.strip():
            body["country"] = region.strip().lower()
        await _status(__event_emitter__, f"Searching the web for {searched}", False)
        try:
            async with httpx.AsyncClient(timeout=600) as client:
                response = await client.post(
                    WEB_SEARCH_ENDPOINT,
                    json=body,
                    headers={
                        "accept": "application/json",
                        "Authorization": f"Bearer {api_key}",
                        "x-api-token": api_key,
                    },
                )
        except httpx.HTTPError:
            await _status(__event_emitter__, "MrScraper search failed", True)
            return f"MrScraper could not {action}: the MrScraper API could not be reached."
        if response.status_code == 401:
            await _status(__event_emitter__, "MrScraper search failed", True)
            return f"MrScraper could not {action}: the API key in this tool's Valves is missing or invalid."
        if response.status_code >= 400:
            await _status(__event_emitter__, "MrScraper search failed", True)
            return f"MrScraper could not {action} (HTTP {response.status_code})."
        results = [
            {
                "rank": entry.get("rank"),
                "title": entry.get("title"),
                "link": entry.get("url"),
                "description": entry.get("description"),
                "source": entry.get("source"),
            }
            for entry in response.json().get("data") or []
            if isinstance(entry, dict)
        ]
        await _status(__event_emitter__, f"Found {len(results)} results", True)
        return json.dumps({"query": searched, "results": results}, ensure_ascii=False)

    async def account_status(self, __event_emitter__: EventEmitter = None) -> str:
        """
        Show the MrScraper account's subscription status, token limit, tokens used, and tokens remaining. Uses no tokens.
        """
        try:
            response = await self._client().account()
        except MrScraperError as error:
            return _failure("read the account", error)
        await _status(__event_emitter__, "Read MrScraper account status", True)
        return json.dumps(response.get("data") or {}, ensure_ascii=False)
