"""
title: MrScraper
author: MrScraper
author_url: https://mrscraper.com
git_url: https://github.com/mrscraper-com/mrscraper-open-webui
description: Retrieve public web pages as Markdown or HTML, search the web, and check MrScraper account usage.
required_open_webui_version: 0.6.11
version: 0.3.0
licence: MIT
"""

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Literal, Optional
from urllib.parse import quote_plus, urlsplit

import httpx
from pydantic import BaseModel, Field

EventEmitter = Optional[Callable[[dict], Awaitable[None]]]
WaitUntil = Literal["domcontentloaded", "load", "networkidle0", "networkidle2"]

# Requests match MrScraper MCP server 0.5.0 (src/api.ts and src/http.ts).
SCRAPER_API_URL = "https://api.mrscraper.com/"
ACCOUNT_URL = "https://api.app.mrscraper.com/api/v1/subscription-accounts"
DEFAULT_JSON_PROMPT = "Extract all data as complete as possible."
MAX_GET_URL_LENGTH = 8000
MAX_SAFE_INTEGER = 2**53 - 1
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


def _integer(name: str, value: Any, minimum: int, maximum: int = MAX_SAFE_INTEGER) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _boolean(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _country(name: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2}", value):
        raise ValueError(f"{name} must be a two-letter country code")
    return value.lower()


def _page_url(value: Any) -> str:
    try:
        parts = urlsplit(value)
        valid = parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("url must be an absolute HTTP or HTTPS URL")
    return value


def _page_params(
    url: Any,
    timeout: Any,
    geo_code: Any,
    proxy_country: Any,
    super_routing: Any,
    wait_until: Any,
    proxy: Any,
    max_retries: Any,
    save_result: Any,
) -> dict[str, Any]:
    """Page controls shared by HTML, Markdown, screenshots, and JSON extraction."""
    if wait_until not in ("domcontentloaded", "load", "networkidle0", "networkidle2"):
        raise ValueError(
            "wait_until must be domcontentloaded, load, networkidle0, or networkidle2"
        )
    params = {
        "url": _page_url(url),
        "timeout": _integer("timeout", timeout, 1, 300),
        "geoCode": _country("geo_code", geo_code),
        "proxyCountry": _country("proxy_country", proxy_country),
        "super": _boolean("super", super_routing),
        "waitUntil": wait_until,
        "saveResult": _boolean("save_result", save_result),
    }
    if proxy is not None:
        params["proxy"] = _text("proxy", proxy)
    if max_retries is not None:
        params["maxRetries"] = _integer("max_retries", max_retries, 0)
    return params


def _loading_params(
    block_resources: Any, wait_for_selector: Any, return_cookie: Any
) -> dict[str, Any]:
    """Loading controls shared by HTML, Markdown, and screenshots."""
    params = {
        "blockResources": _boolean("block_resources", block_resources),
        "returnCookie": _boolean("return_cookie", return_cookie),
    }
    if wait_for_selector is not None:
        params["waitForSelector"] = _text("wait_for_selector", wait_for_selector)
    return params


def _form(value: Any) -> str:
    # Encodes like URLSearchParams in the MCP server, so GET URLs and the 8,000-character limit match.
    text = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return quote_plus(text, safe="*").replace("~", "%7E")


def _endpoint(params: dict[str, Any]) -> str:
    query = "&".join(f"{_form(key)}={_form(value)}" for key, value in params.items())
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
        timeout: int = 300,
        geo_code: str = "us",
        proxy_country: str = "us",
        super: bool = False,
        wait_until: WaitUntil = "domcontentloaded",
        proxy: Optional[str] = None,
        max_retries: Optional[int] = None,
        save_result: bool = False,
        block_resources: bool = False,
        wait_for_selector: Optional[str] = None,
        return_cookie: bool = False,
        browser_rendering: bool = False,
        warm_start: bool = False,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Retrieve the raw HTML of a page. Browser rendering is optional.
        :param url: Absolute HTTP or HTTPS URL of the page to process.
        :param timeout: Page load timeout in seconds, from 1 to 300. The HTTP request allows 30 additional seconds.
        :param geo_code: Two-letter country code to load the page from, sent in lowercase.
        :param proxy_country: Two-letter country code of the proxy server, sent in lowercase.
        :param super: Whether to route the request through real devices.
        :param wait_until: Navigation completion event to wait for: domcontentloaded, load, networkidle0, or networkidle2.
        :param proxy: Custom proxy URL used to load the page.
        :param max_retries: Maximum number of retries, as a nonnegative integer. Omit this parameter for no limit.
        :param save_result: Whether to save the result to the dashboard Result tab.
        :param block_resources: Whether to block images, fonts, and stylesheets while loading the page.
        :param wait_for_selector: Wait for the page to contain the specified CSS selector before returning the result.
        :param return_cookie: Whether to include browser session cookies in the response.
        :param browser_rendering: Whether to render the page in a browser before returning the result.
        :param warm_start: Whether to enable warm start for more reliable scraping, at the cost of additional latency.
        """
        params = {
            **_page_params(
                url,
                timeout,
                geo_code,
                proxy_country,
                super,
                wait_until,
                proxy,
                max_retries,
                save_result,
            ),
            **_loading_params(block_resources, wait_for_selector, return_cookie),
            "browserRendering": _boolean("browser_rendering", browser_rendering),
            "warmStart": _boolean("warm_start", warm_start),
            "html": True,
            "markdown": False,
            "screenshot": False,
        }
        return await _send(
            self.valves,
            __event_emitter__,
            f"Scrape HTML: {url}",
            _endpoint(params),
            params["timeout"] + 30,
        )

    async def scrape_markdown(
        self,
        url: str,
        timeout: int = 300,
        geo_code: str = "us",
        proxy_country: str = "us",
        super: bool = False,
        wait_until: WaitUntil = "domcontentloaded",
        proxy: Optional[str] = None,
        max_retries: Optional[int] = None,
        save_result: bool = False,
        block_resources: bool = False,
        wait_for_selector: Optional[str] = None,
        return_cookie: bool = False,
        browser_rendering: bool = False,
        warm_start: bool = False,
        html: bool = False,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Retrieve a page as Markdown. Including HTML also enables browser rendering and returns both outputs as JSON.
        :param url: Absolute HTTP or HTTPS URL of the page to process.
        :param timeout: Page load timeout in seconds, from 1 to 300. The HTTP request allows 30 additional seconds.
        :param geo_code: Two-letter country code to load the page from, sent in lowercase.
        :param proxy_country: Two-letter country code of the proxy server, sent in lowercase.
        :param super: Whether to route the request through real devices.
        :param wait_until: Navigation completion event to wait for: domcontentloaded, load, networkidle0, or networkidle2.
        :param proxy: Custom proxy URL used to load the page.
        :param max_retries: Maximum number of retries, as a nonnegative integer. Omit this parameter for no limit.
        :param save_result: Whether to save the result to the dashboard Result tab.
        :param block_resources: Whether to block images, fonts, and stylesheets while loading the page.
        :param wait_for_selector: Wait for the page to contain the specified CSS selector before returning the result.
        :param return_cookie: Whether to include browser session cookies in the response.
        :param browser_rendering: Whether to render the page in a browser before returning the result.
        :param warm_start: Whether to enable warm start for more reliable scraping, at the cost of additional latency.
        :param html: Whether to include the page HTML in the response.
        """
        html = _boolean("html", html)
        browser_rendering = _boolean("browser_rendering", browser_rendering)
        params = {
            **_page_params(
                url,
                timeout,
                geo_code,
                proxy_country,
                super,
                wait_until,
                proxy,
                max_retries,
                save_result,
            ),
            **_loading_params(block_resources, wait_for_selector, return_cookie),
            "browserRendering": html or browser_rendering,
            "warmStart": _boolean("warm_start", warm_start),
            "html": html,
            "markdown": True,
            "screenshot": False,
        }
        return await _send(
            self.valves,
            __event_emitter__,
            f"Scrape Markdown: {url}",
            _endpoint(params),
            params["timeout"] + 30,
        )

    async def get_screenshot(
        self,
        url: str,
        timeout: int = 300,
        geo_code: str = "us",
        proxy_country: str = "us",
        super: bool = False,
        wait_until: WaitUntil = "domcontentloaded",
        proxy: Optional[str] = None,
        max_retries: Optional[int] = None,
        save_result: bool = False,
        block_resources: bool = False,
        wait_for_selector: Optional[str] = None,
        return_cookie: bool = False,
        screenshot: Literal["full", "top"] = "full",
        html: bool = True,
        markdown: bool = False,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Capture a base64 screenshot of the full page or top viewport. Browser rendering is always enabled.
        :param url: Absolute HTTP or HTTPS URL of the page to process.
        :param timeout: Page load timeout in seconds, from 1 to 300. The HTTP request allows 30 additional seconds.
        :param geo_code: Two-letter country code to load the page from, sent in lowercase.
        :param proxy_country: Two-letter country code of the proxy server, sent in lowercase.
        :param super: Whether to route the request through real devices.
        :param wait_until: Navigation completion event to wait for: domcontentloaded, load, networkidle0, or networkidle2.
        :param proxy: Custom proxy URL used to load the page.
        :param max_retries: Maximum number of retries, as a nonnegative integer. Omit this parameter for no limit.
        :param save_result: Whether to save the result to the dashboard Result tab.
        :param block_resources: Whether to block images, fonts, and stylesheets while loading the page.
        :param wait_for_selector: Wait for the page to contain the specified CSS selector before returning the result.
        :param return_cookie: Whether to include browser session cookies in the response.
        :param screenshot: Screenshot area to capture: full for the entire page or top for the top viewport.
        :param html: Whether to include the page HTML in the response.
        :param markdown: Whether to include the page Markdown in the response.
        """
        if screenshot not in ("full", "top"):
            raise ValueError("screenshot must be full or top")
        params = {
            **_page_params(
                url,
                timeout,
                geo_code,
                proxy_country,
                super,
                wait_until,
                proxy,
                max_retries,
                save_result,
            ),
            **_loading_params(block_resources, wait_for_selector, return_cookie),
            "browserRendering": True,
            "screenshot": screenshot,
            "html": _boolean("html", html),
            "markdown": _boolean("markdown", markdown),
        }
        return await _send(
            self.valves,
            __event_emitter__,
            f"Get Screenshot: {url}",
            _endpoint(params),
            params["timeout"] + 30,
        )

    async def scrape_brand(
        self, url: str, __event_emitter__: EventEmitter = None
    ) -> str:
        """Extract a website's colors, typography, logos, and social links as JSON.
        :param url: Absolute HTTP or HTTPS URL of the page to process.
        """
        params = {"action": "brand-scraping", "url": _page_url(url), "bundle": "none"}
        return await _send(
            self.valves, __event_emitter__, f"Scrape Brand: {url}", _endpoint(params), 120
        )

    async def scrape_json(
        self,
        url: str,
        timeout: int = 300,
        geo_code: str = "us",
        proxy_country: str = "us",
        super: bool = False,
        wait_until: WaitUntil = "domcontentloaded",
        proxy: Optional[str] = None,
        max_retries: Optional[int] = None,
        save_result: bool = False,
        prompt: str = DEFAULT_JSON_PROMPT,
        browser_rendering: bool = False,
        html: bool = False,
        screenshot: bool = False,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Extract structured JSON from a page using a natural-language prompt. Append Json Schema: and a JSON schema to shape the output.
        :param url: Absolute HTTP or HTTPS URL of the page to process.
        :param timeout: Page load timeout in seconds, from 1 to 300. The HTTP request allows 30 additional seconds.
        :param geo_code: Two-letter country code to load the page from, sent in lowercase.
        :param proxy_country: Two-letter country code of the proxy server, sent in lowercase.
        :param super: Whether to route the request through real devices.
        :param wait_until: Navigation completion event to wait for: domcontentloaded, load, networkidle0, or networkidle2.
        :param proxy: Custom proxy URL used to load the page.
        :param max_retries: Maximum number of retries, as a nonnegative integer. Omit this parameter for no limit.
        :param save_result: Whether to save the result to the dashboard Result tab.
        :param prompt: Instructions describing the data to extract. Append Json Schema: and a JSON schema to shape the output.
        :param browser_rendering: Whether to render the page in a browser before returning the result.
        :param html: Whether to include the page HTML in the response.
        :param screenshot: Whether to include a base64 screenshot in the response.
        """
        _text("prompt", prompt)
        params = {
            **_page_params(
                url,
                timeout,
                geo_code,
                proxy_country,
                super,
                wait_until,
                proxy,
                max_retries,
                save_result,
            ),
            "browserRendering": _boolean("browser_rendering", browser_rendering),
            "html": _boolean("html", html),
            "screenshot": _boolean("screenshot", screenshot),
            "agent": "general",
            "prompt": prompt,
        }
        label = f"Scrape JSON: {url}"
        deadline = params["timeout"] + 30
        get_url = _endpoint(params)
        if len(get_url) <= MAX_GET_URL_LENGTH:
            return await _send(self.valves, __event_emitter__, label, get_url, deadline)
        # Too long for GET: url, prompt, and agent move to a POST body; page controls stay in the query.
        body = {key: params.pop(key) for key in ("url", "prompt", "agent")}
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
        country: str = "us",
        num_results: int = 10,
        __event_emitter__: EventEmitter = None,
    ) -> str:
        """Search the web and return ranked results with titles, URLs, descriptions, and sources.
        :param query: Search query text, trimmed before sending, containing 1 to 1000 characters.
        :param country: Two-letter country code to search from, sent in lowercase.
        :param num_results: Number of search results to request, as an integer from 10 to 100. Fewer results may be returned.
        """
        query = _text("query", query)
        if len(query) > 1000:
            raise ValueError("query must be 1 to 1000 characters")
        params = {
            "action": "websearch",
            "query": query,
            "country": _country("country", country),
            "numResults": _integer("num_results", num_results, 10, 100),
        }
        return await _send(
            self.valves, __event_emitter__, f"Web Search: {query}", _endpoint(params), 120
        )
