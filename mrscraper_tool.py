"""
title: MrScraper
author: MrScraper
author_url: https://mrscraper.com
git_url: https://github.com/mrscraper-com/mrscraper-open-webui
description: Retrieve public web pages as Markdown or HTML, search the web, and check MrScraper account usage.
required_open_webui_version: 0.6.11
version: 0.4.0
licence: MIT
"""

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote_plus, urlsplit

import httpx
from pydantic import BaseModel, Field

EventEmitter = Optional[Callable[[dict], Awaitable[None]]]

# Inputs follow MrScraper's API docs and integration parameter spec, like MrScraper MCP server 0.6.0:
# an optional parameter left as None is not sent, so the API's default applies. Headers, deadlines,
# and the response envelope match the MCP server's src/http.ts.
SCRAPER_API_URL = "https://api.mrscraper.com/"
ACCOUNT_URL = "https://api.app.mrscraper.com/api/v1/subscription-accounts"
MAX_GET_URL_LENGTH = 8000
MAX_SAFE_INTEGER = 2**53 - 1
MAX_AGE_MS = 604_800_000
# The page timeout the API applies when none is set; the HTTP deadline adds 30 seconds.
DEFAULT_PAGE_TIMEOUT = 300
SCREENSHOT_AREA = re.compile(r"full|top|([0-9]{3,4})x([0-9]{3,4})(?:-([0-9]{2,3}))?")
KEY_HELP = "Set api_key in this tool's Valves to a key from https://app.mrscraper.com/api-tokens."

# Response headers that carry credentials or internal routing details.
HIDDEN_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "set-cookie",
        "set-cookie2",
        "x-api-token",
        "x-discard-token-secret",
        "x-worker-name",
    }
)
# JSON fields redacted from scraping and search responses; status returns its body unchanged.
SENSITIVE_KEYS = frozenset(
    {
        "accesstoken",
        "api_key",
        "apikey",
        "apitoken",
        "authorization",
        "cookie",
        "latestapitoken",
        "password",
        "refreshtoken",
        "secret",
        "set-cookie",
        "token",
        "x-api-token",
        "x-discard-token-secret",
    }
)
# A "data" field next to any of these keys holds extracted page data and is kept as returned.
EXTRACTED_DATA_MARKERS = ("id", "scraperId", "status", "type", "url", "runtime", "tokenUsage")
CREDENTIAL_PATTERNS = (
    (re.compile(r"\batk_[A-Za-z0-9_-]{12,}\b", re.ASCII), "[REDACTED_API_TOKEN]"),
    (re.compile(r"(authorization\s*:\s*bearer\s+)[^\s'\"\\]+", re.I), r"\1[REDACTED]"),
    (re.compile(r"(x-api-token\s*:\s*)[^\s'\"\\]+", re.I), r"\1[REDACTED]"),
    (
        re.compile(
            r"([?&](?:token|api[_-]?key|signature|sig|x-amz-(?:credential|security-token|signature))=)"
            r"[^&\s'\"\\]+",
            re.I,
        ),
        r"\1[REDACTED]",
    ),
)
JWT_SHAPE = re.compile(r"[\w-]+\.[\w-]+\.[\w-]+", re.ASCII)


async def _emit_status(emitter: EventEmitter, description: str, done: bool) -> None:
    if emitter:
        await emitter(
            {"type": "status", "data": {"description": description, "done": done}}
        )


# Each validator returns `default` for None (a parameter the model left unset) and raises
# ValueError for an invalid value, before any request is sent.
def _integer(
    name: str,
    value: Any,
    minimum: int,
    maximum: int = MAX_SAFE_INTEGER,
    default: Optional[int] = None,
) -> Optional[int]:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _boolean(name: str, value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _text(name: str, value: Any, maximum: Optional[int] = None) -> Optional[str]:
    """A non-blank string, trimmed, with at most `maximum` characters."""
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    if maximum is not None and len(text) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return text


def _country(name: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2}", value):
        raise ValueError(f"{name} must be a two-letter country code")
    return value.lower()


def _screenshot(value: Any, default: Optional[str] = None) -> Optional[str]:
    """full, top, or <width>x<height> (100 to 3840) with an optional -<zoom> (25 to 300)."""
    if value is None:
        return default
    match = SCREENSHOT_AREA.fullmatch(value) if isinstance(value, str) else None
    width, height, zoom = match.groups() if match else (None, None, None)
    valid = match is not None and (
        width is None
        or (
            100 <= int(width) <= 3840
            and 100 <= int(height) <= 3840
            and (zoom is None or 25 <= int(zoom) <= 300)
        )
    )
    if not valid:
        raise ValueError(
            "screenshot must be full, top, or <width>x<height> with an optional -<zoom>,"
            " such as 1280x800 or 1920x1080-75 (width and height 100 to 3840, zoom 25 to 300)"
        )
    return value


def _page_url(value: Any) -> str:
    try:
        parts = urlsplit(value)
        valid = parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("url must be an absolute HTTP or HTTPS URL")
    return value


def _page_deadline(timeout: Optional[int]) -> int:
    return (DEFAULT_PAGE_TIMEOUT if timeout is None else timeout) + 30


def _form(value: Any) -> str:
    # Encodes like URLSearchParams in the MCP server, so GET URLs and the 8,000-character limit match.
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return quote_plus(text, safe="*").replace("~", "%7E")


def _endpoint(params: dict[str, Any]) -> str:
    """The Scraper API URL with the set values in order; None values are left out."""
    query = "&".join(
        f"{_form(key)}={_form(value)}" for key, value in params.items() if value is not None
    )
    return f"{SCRAPER_API_URL}?{query}"


def _auth_headers(api_key: str) -> dict[str, str]:
    # The MCP server's request headers. An OAuth access token (a JWT) is sent only as a bearer token.
    headers = {
        "User-Agent": "node",
        "Accept-Language": "*",
        "Sec-Fetch-Mode": "cors",
        "Authorization": f"Bearer {api_key}",
    }
    if not JWT_SHAPE.fullmatch(api_key):
        headers["x-api-token"] = api_key
    return headers


def _redact(text: str) -> str:
    for pattern, replacement in CREDENTIAL_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize(value: Any) -> Any:
    """Redacts credential fields in a JSON body; extracted page data is kept as returned."""
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if not isinstance(value, dict):
        return value
    extracted = any(marker in value for marker in EXTRACTED_DATA_MARKERS)
    sanitized = {}
    for key, item in value.items():
        name = key.lower()
        if name == "data" and extracted:
            sanitized[key] = item
        elif name in SENSITIVE_KEYS:
            sanitized[key] = "[REDACTED]"
        elif name == "curl" and isinstance(item, str):
            sanitized[key] = _redact(item)
        else:
            sanitized[key] = _sanitize(item)
    return sanitized


async def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    deadline: int,
    body: Optional[dict[str, Any]] = None,
    sanitize: bool = True,
) -> dict[str, Any]:
    """Sends one request and returns the response envelope: status_code, data, headers, and error on failure."""
    content = None
    if body is not None:
        headers = {**headers, "Content-Type": "application/json", "accept": "application/json"}
        content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=deadline) as client:
            response = await asyncio.wait_for(
                client.request(method, url, headers=headers, content=content), deadline
            )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return {
            "error": f"Request timed out after {deadline}s",
            "status_code": None,
            "data": None,
            "headers": {},
        }
    except Exception as error:
        return {
            "error": _redact(str(error) or type(error).__name__),
            "status_code": None,
            "data": None,
            "headers": {},
        }
    text = response.content.decode("utf-8-sig", errors="replace")
    data: Any = text
    if "application/json" in response.headers.get("content-type", "").lower():
        try:
            parsed = json.loads(text) if text else None
        except ValueError:
            pass  # Not JSON after all: the text is returned as it is.
        else:
            data = _sanitize(parsed) if sanitize else parsed
    safe_headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in HIDDEN_HEADERS
    }
    if response.status_code == 401:
        return {
            "error": f"Unauthorized or invalid token. {KEY_HELP}",
            "status_code": response.status_code,
            "data": data,
            "headers": safe_headers,
        }
    if not response.is_success:
        return {
            "error": f"HTTP {response.status_code}",
            "status_code": response.status_code,
            "data": data,
            "headers": safe_headers,
        }
    return {"status_code": response.status_code, "data": data, "headers": safe_headers}


async def _send(
    valves: Any,
    emitter: EventEmitter,
    label: str,
    url: str,
    deadline: int,
    method: str = "GET",
    body: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    sanitize: bool = True,
) -> str:
    """Calls MrScraper with the Valves' API key and returns the envelope as JSON text for the model."""
    api_key = re.sub(r"^bearer\s+", "", valves.api_key.strip(), flags=re.I).strip()
    if not api_key:
        raise ValueError(f"MrScraper API key is not set. {KEY_HELP}")
    await _emit_status(emitter, label, False)
    result = await _request(
        method,
        url,
        {**(headers or {}), **_auth_headers(api_key)},
        deadline,
        body=body,
        sanitize=sanitize,
    )
    outcome = f"{label} failed: {result['error']}" if "error" in result else label
    await _emit_status(emitter, outcome, True)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    limit = valves.max_characters
    if 0 < limit < len(text):
        return (
            f"{text[:limit]}\n\n[Truncated to {limit} characters; raise max_characters"
            " in the tool's Valves to get more.]"
        )
    return text


# Open WebUI turns every method of Tools into a tool (before 0.9, "_" methods too), so helpers live outside it.
class Tools:
    class Valves(BaseModel):
        api_key: str = Field(
            default="",
            description="MrScraper API key from https://app.mrscraper.com/api-tokens",
        )
        max_characters: int = Field(
            default=100_000,
            description="Longest result returned to the model; 0 returns the whole result.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def status(self, __event_emitter__: EventEmitter = None) -> str:
        """Return the raw account status API response without summarizing, redacting body fields, or adding calculated fields."""
        return await _send(
            self.valves,
            __event_emitter__,
            "Account Status",
            ACCOUNT_URL,
            600,
            headers={"accept": "application/json"},
            sanitize=False,
        )

    async def scrape_html(
        self,
        url: str,
        browser_rendering: Optional[bool] = None,
        timeout: Optional[int] = None,
        warm_start: Optional[bool] = None,
        block_resources: Optional[bool] = None,
        wait_for_selector: Optional[str] = None,
        geo_code: Optional[str] = None,
        max_age: int = 0,
        max_retries: Optional[int] = None,
        screenshot: Optional[str] = None,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Retrieve the raw HTML of a page. Browser rendering and a screenshot are optional.
        :param url: URL of the page to scrape. Must include the protocol (https://).
        :param browser_rendering: Controls whether the page is loaded in a browser. Leave it out to load the page without a browser first and switch to a browser automatically if the request is blocked. Set true to always load the page in a browser, which JavaScript-heavy sites, SPAs, and pages that load content dynamically need. Set false to never use a browser.
        :param timeout: Maximum seconds to wait for the scrape before the request times out, from 1 to 330. Increase for slow or complex pages. Defaults to 300.
        :param warm_start: Opens the site's home page first to warm the session before loading the target URL. Useful for sites that expect a normal browsing session before deeper pages.
        :param block_resources: Blocks images, fonts, and stylesheets from loading. Speeds up scraping and reduces bandwidth usage.
        :param wait_for_selector: Waits until a specific CSS selector or text appears on the page before returning it, such as #results. Up to 500 characters. Useful for content that loads asynchronously after the initial page load.
        :param geo_code: Two-letter country code to load the page from, such as us or gb. Defaults to the United States.
        :param max_age: Serves a cached result of the same request when it is newer than this age, in milliseconds, such as 86400000 for 1 day. Up to 604800000 (7 days). 0, the default, always scrapes fresh. A cached result costs the same tokens as the original scrape.
        :param max_retries: Maximum number of retries for the request. Omit for no limit.
        :param screenshot: Also captures a screenshot of the rendered page as a base64-encoded PNG: full captures the entire scroll height, top captures the first viewport, and <width>x<height> sets the viewport size in pixels with an optional -<zoom> percent, such as 1280x800 or 1920x1080-75. Width and height range from 100 to 3840, zoom from 25 to 300. Setting it forces browser rendering. Leave it out for no screenshot.
        """
        timeout = _integer("timeout", timeout, 1, 330)
        params = {
            "url": _page_url(url),
            "browserRendering": _boolean("browser_rendering", browser_rendering),
            "timeout": timeout,
            "warmStart": _boolean("warm_start", warm_start),
            "blockResources": _boolean("block_resources", block_resources),
            "waitForSelector": _text("wait_for_selector", wait_for_selector, 500),
            "geoCode": _country("geo_code", geo_code),
            "maxAge": _integer("max_age", max_age, 0, MAX_AGE_MS, default=0),
            "maxRetries": _integer("max_retries", max_retries, 0),
            "screenshot": _screenshot(screenshot),
            "html": True,
            "markdown": False,
            "saveResult": True,
        }
        return await _send(
            self.valves,
            __event_emitter__,
            f"Scrape HTML: {url}",
            _endpoint(params),
            _page_deadline(timeout),
        )

    async def scrape_markdown(
        self,
        url: str,
        browser_rendering: Optional[bool] = None,
        timeout: Optional[int] = None,
        geo_code: Optional[str] = None,
        warm_start: Optional[bool] = None,
        block_resources: Optional[bool] = None,
        wait_for_selector: Optional[str] = None,
        max_age: int = 0,
        max_retries: Optional[int] = None,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Retrieve a page as Markdown. Browser rendering is optional.
        :param url: URL of the page to scrape. Must include the protocol (https://).
        :param browser_rendering: Controls whether the page is loaded in a browser. Leave it out to load the page without a browser first and switch to a browser automatically if the request is blocked. Set true to always load the page in a browser, which JavaScript-heavy sites, SPAs, and pages that load content dynamically need. Set false to never use a browser.
        :param timeout: Maximum seconds to wait for the scrape before the request times out, from 1 to 330. Increase for slow or complex pages. Defaults to 300.
        :param geo_code: Two-letter country code to load the page from, such as us or gb. Defaults to the United States.
        :param warm_start: Opens the site's home page first to warm the session before loading the target URL. Useful for sites that expect a normal browsing session before deeper pages.
        :param block_resources: Blocks images, fonts, and stylesheets from loading. Speeds up scraping and reduces bandwidth usage.
        :param wait_for_selector: Waits until a specific CSS selector or text appears on the page before returning it, such as #results. Up to 500 characters. Useful for content that loads asynchronously after the initial page load.
        :param max_age: Serves a cached result of the same request when it is newer than this age, in milliseconds, such as 86400000 for 1 day. Up to 604800000 (7 days). 0, the default, always scrapes fresh. A cached result costs the same tokens as the original scrape.
        :param max_retries: Maximum number of retries for the request. Omit for no limit.
        """
        timeout = _integer("timeout", timeout, 1, 330)
        params = {
            "url": _page_url(url),
            "browserRendering": _boolean("browser_rendering", browser_rendering),
            "timeout": timeout,
            "geoCode": _country("geo_code", geo_code),
            "warmStart": _boolean("warm_start", warm_start),
            "blockResources": _boolean("block_resources", block_resources),
            "waitForSelector": _text("wait_for_selector", wait_for_selector, 500),
            "maxAge": _integer("max_age", max_age, 0, MAX_AGE_MS, default=0),
            "maxRetries": _integer("max_retries", max_retries, 0),
            "markdown": True,
            "html": False,
            "screenshot": False,
            "saveResult": True,
        }
        return await _send(
            self.valves,
            __event_emitter__,
            f"Scrape Markdown: {url}",
            _endpoint(params),
            _page_deadline(timeout),
        )

    async def get_screenshot(
        self,
        url: str,
        screenshot: str = "full",
        timeout: Optional[int] = None,
        geo_code: Optional[str] = None,
        wait_for_selector: Optional[str] = None,
        max_age: int = 0,
        max_retries: Optional[int] = None,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Capture a base64 PNG screenshot of the full page, the top viewport, or a custom viewport size. Browser rendering is always enabled.
        :param url: URL of the page to scrape. Must include the protocol (https://).
        :param screenshot: Screenshot area: full captures the entire scroll height (the default), top captures the first viewport, and <width>x<height> sets the viewport size in pixels with an optional -<zoom> percent, such as 1280x800 or 1920x1080-75. Width and height range from 100 to 3840, zoom from 25 to 300. The image is a base64-encoded PNG.
        :param timeout: Maximum seconds to wait for the scrape before the request times out, from 1 to 330. Increase for slow or complex pages. Defaults to 300.
        :param geo_code: Two-letter country code to load the page from, such as us or gb. Defaults to the United States.
        :param wait_for_selector: Waits until a specific CSS selector or text appears on the page before returning it, such as #results. Up to 500 characters. Useful for content that loads asynchronously after the initial page load.
        :param max_age: Serves a cached result of the same request when it is newer than this age, in milliseconds, such as 86400000 for 1 day. Up to 604800000 (7 days). 0, the default, always scrapes fresh. A cached result costs the same tokens as the original scrape.
        :param max_retries: Maximum number of retries for the request. Omit for no limit.
        """
        timeout = _integer("timeout", timeout, 1, 330)
        params = {
            "url": _page_url(url),
            "screenshot": _screenshot(screenshot, default="full"),
            "timeout": timeout,
            "geoCode": _country("geo_code", geo_code),
            "waitForSelector": _text("wait_for_selector", wait_for_selector, 500),
            "maxAge": _integer("max_age", max_age, 0, MAX_AGE_MS, default=0),
            "maxRetries": _integer("max_retries", max_retries, 0),
            "browserRendering": True,
            "html": False,
            "markdown": False,
            "saveResult": True,
        }
        return await _send(
            self.valves,
            __event_emitter__,
            f"Get Screenshot: {url}",
            _endpoint(params),
            _page_deadline(timeout),
        )

    async def scrape_brand(
        self, url: str, __event_emitter__: EventEmitter = None
    ) -> str:
        """Extract a website's colors, typography, logos, and social links as JSON.
        :param url: URL of the page to scrape. Must include the protocol (https://).
        """
        params = {"action": "brand-scraping", "url": _page_url(url), "bundle": "none"}
        return await _send(
            self.valves, __event_emitter__, f"Scrape Brand: {url}", _endpoint(params), 120
        )

    async def scrape_json(
        self,
        url: str,
        prompt: Optional[str] = None,
        html: bool = True,
        screenshot: bool = False,
        browser_rendering: Optional[bool] = None,
        wait_for_selector: Optional[str] = None,
        warm_start: Optional[bool] = None,
        block_resources: Optional[bool] = None,
        timeout: Optional[int] = None,
        geo_code: Optional[str] = None,
        max_age: int = 0,
        max_retries: Optional[int] = None,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Extract structured JSON from a page using a natural-language prompt. Append Json Schema: and a JSON schema to shape the output.
        :param url: URL of the page to scrape. Must include the protocol (https://).
        :param prompt: Description of the data shape you want back, for example fields like title, price, and rating. The prompt only parses the scraped content; it cannot instruct the scraper itself. To shape the output with a JSON schema, append it to the prompt as a Json Schema: section. Defaults to "Extract all data as complete as possible."
        :param html: Returns the raw HTML of the scraped page in the response. On by default; set false to leave it out.
        :param screenshot: Captures a screenshot of the rendered page as a base64-encoded image. Useful for verifying what the scraper actually saw. Off by default.
        :param browser_rendering: Controls whether the page is loaded in a browser. Leave it out to load the page without a browser first and switch to a browser automatically if the request is blocked. Set true to always load the page in a browser, which JavaScript-heavy sites, SPAs, and pages that load content dynamically need. Set false to never use a browser.
        :param wait_for_selector: Waits until a specific CSS selector or text appears on the page before returning it, such as #results. Up to 500 characters. Useful for content that loads asynchronously after the initial page load.
        :param warm_start: Opens the site's home page first to warm the session before loading the target URL. Useful for sites that expect a normal browsing session before deeper pages.
        :param block_resources: Blocks images, fonts, and stylesheets from loading. Speeds up scraping and reduces bandwidth usage.
        :param timeout: Maximum seconds to wait for the scrape before the request times out, from 1 to 330. Increase for slow or complex pages. Defaults to 300.
        :param geo_code: Two-letter country code to load the page from, such as us or gb. Defaults to the United States.
        :param max_age: Serves a cached result of the same request when it is newer than this age, in milliseconds, such as 86400000 for 1 day. Up to 604800000 (7 days). 0, the default, always scrapes fresh. A cached result costs the same tokens as the original scrape.
        :param max_retries: Maximum number of retries for the request. Omit for no limit.
        """
        if prompt is not None:
            _text("prompt", prompt)  # Validated as non-blank; sent as written.
        timeout = _integer("timeout", timeout, 1, 330)
        params = {
            "url": _page_url(url),
            "prompt": prompt,
            "html": _boolean("html", html, default=True),
            "screenshot": _boolean("screenshot", screenshot, default=False),
            "browserRendering": _boolean("browser_rendering", browser_rendering),
            "waitForSelector": _text("wait_for_selector", wait_for_selector, 500),
            "warmStart": _boolean("warm_start", warm_start),
            "blockResources": _boolean("block_resources", block_resources),
            "timeout": timeout,
            "geoCode": _country("geo_code", geo_code),
            "maxAge": _integer("max_age", max_age, 0, MAX_AGE_MS, default=0),
            "maxRetries": _integer("max_retries", max_retries, 0),
            "saveResult": True,
            "agent": "general",
        }
        label = f"Scrape JSON: {url}"
        deadline = _page_deadline(timeout)
        get_url = _endpoint(params)
        if len(get_url) <= MAX_GET_URL_LENGTH:
            return await _send(self.valves, __event_emitter__, label, get_url, deadline)
        # Too long for GET: url, prompt, and agent move to a POST body; the other values stay in the query.
        body = {
            key: params.pop(key)
            for key in ("url", "prompt", "agent")
            if params[key] is not None
        }
        return await _send(
            self.valves,
            __event_emitter__,
            label,
            _endpoint(params),
            deadline,
            method="POST",
            body=body,
        )

    async def web_search(
        self,
        query: str,
        country: Optional[str] = None,
        num_results: Optional[int] = None,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Search the web and return ranked results with titles, URLs, descriptions, and sources.
        :param query: The search query, from 1 to 1000 characters.
        :param country: Two-letter country code to run the search from. Defaults to the United States.
        :param num_results: How many results to return, from 10 to 100. The search may return fewer. Defaults to 10.
        """
        if query is None:
            raise ValueError("query must be a non-empty string")
        query = _text("query", query, 1000)
        params = {
            "action": "websearch",
            "query": query,
            "country": _country("country", country),
            "numResults": _integer("num_results", num_results, 10, 100),
        }
        return await _send(
            self.valves, __event_emitter__, f"Web Search: {query}", _endpoint(params), 120
        )
