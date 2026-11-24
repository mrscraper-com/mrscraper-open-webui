"""Stubbed tests for mrscraper_tool.py: the exact request of each function. No API calls.

Run from the repository root:
    uv run --with httpx --with pydantic --with pytest pytest -q
"""

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mrscraper_tool

KEY = "atk_testtesttesttest"
# Placeholder values from MrScraper's integration examples.
PAGE_URL = "https://www.scrapethissite.com"
BRAND_URL = "https://mrscraper.com"
JSON_URL = "https://mrscraper.com/pricing"
API = "https://api.mrscraper.com/"
REMOVED = ("super", "proxy", "proxy_country", "wait_until", "return_cookie", "save_result", "token_cap", "action")


class Api:
    """Records every request the tool sends and answers it without the network."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.deadlines: list[float] = []
        self.response = httpx.Response(200, json={"success": True})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response

    def sent(self) -> httpx.Request:
        assert len(self.requests) == 1
        return self.requests[0]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Api:
    recorder = Api()
    real_client = httpx.AsyncClient

    def client(**kwargs):
        recorder.deadlines.append(kwargs["timeout"])
        return real_client(transport=httpx.MockTransport(recorder.handle), **kwargs)

    monkeypatch.setattr(mrscraper_tool.httpx, "AsyncClient", client)
    return recorder


@pytest.fixture
def tools() -> mrscraper_tool.Tools:
    instance = mrscraper_tool.Tools()
    instance.valves.api_key = KEY
    return instance


def run(coroutine) -> dict:
    return json.loads(asyncio.run(coroutine))


def query(request: httpx.Request) -> list[tuple[str, str]]:
    return parse_qsl(request.url.query.decode(), keep_blank_values=True)


# Defaults only: exactly what each function sends when the model sets nothing optional.
def test_status(api, tools):
    api.response = httpx.Response(200, json={"data": {"token": "kept", "tokenLimit": 1}})
    result = run(tools.status())
    request = api.sent()
    assert (request.method, str(request.url)) == ("GET", "https://api.app.mrscraper.com/api/v1/subscription-accounts")
    assert request.headers["x-api-token"] == KEY
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert result["data"] == {"data": {"token": "kept", "tokenLimit": 1}}
    assert api.deadlines == [600]


def test_scrape_html_defaults(api, tools):
    run(tools.scrape_html(PAGE_URL))
    request = api.sent()
    assert request.method == "GET"
    assert str(request.url) == (
        f"{API}?url=https%3A%2F%2Fwww.scrapethissite.com&maxAge=0&html=true&markdown=false&saveResult=true"
    )
    assert request.content == b""
    assert api.deadlines == [330]


def test_scrape_markdown_defaults(api, tools):
    run(tools.scrape_markdown(PAGE_URL))
    assert str(api.sent().url) == (
        f"{API}?url=https%3A%2F%2Fwww.scrapethissite.com&maxAge=0"
        "&markdown=true&html=false&screenshot=false&saveResult=true"
    )


def test_get_screenshot_defaults(api, tools):
    run(tools.get_screenshot(PAGE_URL))
    assert str(api.sent().url) == (
        f"{API}?url=https%3A%2F%2Fwww.scrapethissite.com&screenshot=full&maxAge=0"
        "&browserRendering=true&html=false&markdown=false&saveResult=true"
    )


def test_scrape_brand(api, tools):
    run(tools.scrape_brand(BRAND_URL))
    assert str(api.sent().url) == f"{API}?action=brand-scraping&url=https%3A%2F%2Fmrscraper.com&bundle=none"
    assert api.deadlines == [120]


def test_scrape_json_defaults(api, tools):
    run(tools.scrape_json(JSON_URL))
    request = api.sent()
    assert request.method == "GET"
    assert str(request.url) == (
        f"{API}?url=https%3A%2F%2Fmrscraper.com%2Fpricing&html=true&screenshot=false&maxAge=0"
        "&saveResult=true&agent=general"
    )


def test_web_search_defaults(api, tools):
    run(tools.web_search("  mrscraper pricing  "))
    assert str(api.sent().url) == f"{API}?action=websearch&query=mrscraper+pricing"
    assert api.deadlines == [120]


def test_none_means_not_set(api, tools):
    """Open WebUI passes null from the model as None: the parameter is left out, or gets its default."""
    run(
        tools.scrape_json(
            JSON_URL, prompt=None, html=None, screenshot=None, browser_rendering=None, timeout=None, max_age=None
        )
    )
    run(tools.get_screenshot(PAGE_URL, screenshot=None, max_age=None, geo_code=None))
    run(tools.web_search("mrscraper", country=None, num_results=None))
    assert [query(request) for request in api.requests] == [
        [
            ("url", JSON_URL),
            ("html", "true"),
            ("screenshot", "false"),
            ("maxAge", "0"),
            ("saveResult", "true"),
            ("agent", "general"),
        ],
        [
            ("url", PAGE_URL),
            ("screenshot", "full"),
            ("maxAge", "0"),
            ("browserRendering", "true"),
            ("html", "false"),
            ("markdown", "false"),
            ("saveResult", "true"),
        ],
        [("action", "websearch"), ("query", "mrscraper")],
    ]


# Every input set: the query keeps the spec's order, and country codes go out in lowercase.
def test_scrape_html_all_inputs(api, tools):
    run(
        tools.scrape_html(
            PAGE_URL,
            browser_rendering=False,
            timeout=330,
            warm_start=True,
            block_resources=True,
            wait_for_selector="#results",
            geo_code="GB",
            max_age=86400000,
            max_retries=0,
            screenshot="1920x1080-75",
        )
    )
    assert query(api.sent()) == [
        ("url", PAGE_URL),
        ("browserRendering", "false"),
        ("timeout", "330"),
        ("warmStart", "true"),
        ("blockResources", "true"),
        ("waitForSelector", "#results"),
        ("geoCode", "gb"),
        ("maxAge", "86400000"),
        ("maxRetries", "0"),
        ("screenshot", "1920x1080-75"),
        ("html", "true"),
        ("markdown", "false"),
        ("saveResult", "true"),
    ]
    assert api.deadlines == [360]


def test_scrape_markdown_all_inputs(api, tools):
    run(
        tools.scrape_markdown(
            PAGE_URL,
            browser_rendering=True,
            timeout=120,
            geo_code="ID",
            warm_start=False,
            block_resources=False,
            wait_for_selector="main",
            max_age=3600000,
            max_retries=2,
        )
    )
    assert query(api.sent()) == [
        ("url", PAGE_URL),
        ("browserRendering", "true"),
        ("timeout", "120"),
        ("geoCode", "id"),
        ("warmStart", "false"),
        ("blockResources", "false"),
        ("waitForSelector", "main"),
        ("maxAge", "3600000"),
        ("maxRetries", "2"),
        ("markdown", "true"),
        ("html", "false"),
        ("screenshot", "false"),
        ("saveResult", "true"),
    ]
    assert api.deadlines == [150]


def test_get_screenshot_all_inputs(api, tools):
    run(
        tools.get_screenshot(
            PAGE_URL,
            screenshot="1280x800",
            timeout=60,
            geo_code="us",
            wait_for_selector=".chart",
            max_age=0,
            max_retries=1,
        )
    )
    assert query(api.sent()) == [
        ("url", PAGE_URL),
        ("screenshot", "1280x800"),
        ("timeout", "60"),
        ("geoCode", "us"),
        ("waitForSelector", ".chart"),
        ("maxAge", "0"),
        ("maxRetries", "1"),
        ("browserRendering", "true"),
        ("html", "false"),
        ("markdown", "false"),
        ("saveResult", "true"),
    ]
    assert api.deadlines == [90]


def test_scrape_json_all_inputs(api, tools):
    run(
        tools.scrape_json(
            JSON_URL,
            prompt="Extract each plan with name and price.",
            html=False,
            screenshot=True,
            browser_rendering=True,
            wait_for_selector="#pricing",
            warm_start=True,
            block_resources=True,
            timeout=200,
            geo_code="SG",
            max_age=1000,
            max_retries=3,
        )
    )
    assert query(api.sent()) == [
        ("url", JSON_URL),
        ("prompt", "Extract each plan with name and price."),
        ("html", "false"),
        ("screenshot", "true"),
        ("browserRendering", "true"),
        ("waitForSelector", "#pricing"),
        ("warmStart", "true"),
        ("blockResources", "true"),
        ("timeout", "200"),
        ("geoCode", "sg"),
        ("maxAge", "1000"),
        ("maxRetries", "3"),
        ("saveResult", "true"),
        ("agent", "general"),
    ]
    assert api.deadlines == [230]


def test_web_search_all_inputs(api, tools):
    run(tools.web_search("mrscraper", country="ID", num_results=20))
    assert query(api.sent()) == [("action", "websearch"), ("query", "mrscraper"), ("country", "id"), ("numResults", "20")]


def test_scrape_json_switches_to_post_above_8000_characters(api, tools):
    fixed = f"{API}?url=https%3A%2F%2Fmrscraper.com%2Fpricing&prompt=&html=true&screenshot=false&maxAge=0&saveResult=true&agent=general"
    longest = "x" * (8000 - len(fixed))
    run(tools.scrape_json(JSON_URL, prompt=longest))
    run(tools.scrape_json(JSON_URL, prompt=longest + "x"))
    get, post = api.requests
    assert (get.method, len(str(get.url))) == ("GET", 8000)
    assert post.method == "POST"
    assert str(post.url) == f"{API}?html=true&screenshot=false&maxAge=0&saveResult=true"
    assert json.loads(post.content) == {"url": JSON_URL, "prompt": longest + "x", "agent": "general"}
    assert post.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    ("function", "kwargs"),
    [
        ("scrape_html", {"url": "ftp://example.com"}),
        ("scrape_html", {"url": PAGE_URL, "timeout": 0}),
        ("scrape_html", {"url": PAGE_URL, "timeout": 331}),
        ("scrape_html", {"url": PAGE_URL, "timeout": True}),
        ("scrape_html", {"url": PAGE_URL, "geo_code": "usa"}),
        ("scrape_html", {"url": PAGE_URL, "max_age": -1}),
        ("scrape_html", {"url": PAGE_URL, "max_age": 604800001}),
        ("scrape_html", {"url": PAGE_URL, "max_retries": -1}),
        ("scrape_html", {"url": PAGE_URL, "browser_rendering": "true"}),
        ("scrape_html", {"url": PAGE_URL, "wait_for_selector": "  "}),
        ("scrape_html", {"url": PAGE_URL, "wait_for_selector": "x" * 501}),
        ("scrape_html", {"url": PAGE_URL, "screenshot": "viewport"}),
        ("scrape_html", {"url": PAGE_URL, "screenshot": "99x800"}),
        ("scrape_html", {"url": PAGE_URL, "screenshot": "1280x3841"}),
        ("scrape_html", {"url": PAGE_URL, "screenshot": "12800x800"}),
        ("scrape_html", {"url": PAGE_URL, "screenshot": "1280x800-24"}),
        ("scrape_html", {"url": PAGE_URL, "screenshot": "1280x800-301"}),
        ("get_screenshot", {"url": PAGE_URL, "screenshot": "FULL"}),
        ("scrape_json", {"url": JSON_URL, "prompt": "   "}),
        ("scrape_json", {"url": JSON_URL, "html": "false"}),
        ("web_search", {"query": "   "}),
        ("web_search", {"query": "x" * 1001}),
        ("web_search", {"query": "mrscraper", "country": "usa"}),
        ("web_search", {"query": "mrscraper", "num_results": 9}),
        ("web_search", {"query": "mrscraper", "num_results": 101}),
    ],
)
def test_invalid_inputs_raise_before_any_request(api, tools, function, kwargs):
    with pytest.raises(ValueError):
        asyncio.run(getattr(tools, function)(**kwargs))
    assert api.requests == []


def test_range_limits_are_accepted(api, tools):
    run(tools.scrape_html(PAGE_URL, timeout=1, max_age=604800000, wait_for_selector="x" * 500, screenshot="100x100-25"))
    run(tools.get_screenshot(PAGE_URL, screenshot="3840x3840-300"))
    run(tools.web_search("x" * 1000, num_results=100))
    assert len(api.requests) == 3


# Parameter names and descriptions from MrScraper's integration parameter spec (2026-11-24).
DESCRIPTIONS = {
    "url": "URL of the page to scrape. Must include the protocol (https://).",
    "browser_rendering": (
        "Controls whether the page is loaded in a browser. Leave it out to load the page without a browser"
        " first and switch to a browser automatically if the request is blocked. Set true to always load the"
        " page in a browser, which JavaScript-heavy sites, SPAs, and pages that load content dynamically need."
        " Set false to never use a browser."
    ),
    "timeout": (
        "Maximum seconds to wait for the scrape before the request times out, from 1 to 330. Increase for"
        " slow or complex pages. Defaults to 300."
    ),
    "warm_start": (
        "Opens the site's home page first to warm the session before loading the target URL. Useful for"
        " sites that expect a normal browsing session before deeper pages."
    ),
    "block_resources": (
        "Blocks images, fonts, and stylesheets from loading. Speeds up scraping and reduces bandwidth usage."
    ),
    "wait_for_selector": (
        "Waits until a specific CSS selector or text appears on the page before returning it, such as"
        " #results. Up to 500 characters. Useful for content that loads asynchronously after the initial page"
        " load."
    ),
    "geo_code": "Two-letter country code to load the page from, such as us or gb. Defaults to the United States.",
    "max_age": (
        "Serves a cached result of the same request when it is newer than this age, in milliseconds, such as"
        " 86400000 for 1 day. Up to 604800000 (7 days). 0, the default, always scrapes fresh. A cached result"
        " costs the same tokens as the original scrape."
    ),
    "max_retries": "Maximum number of retries for the request. Omit for no limit.",
    "query": "The search query, from 1 to 1000 characters.",
    "country": "Two-letter country code to run the search from. Defaults to the United States.",
    "num_results": "How many results to return, from 10 to 100. The search may return fewer. Defaults to 10.",
}
SCREENSHOT_DESCRIPTIONS = {
    "scrape_html": (
        "Also captures a screenshot of the rendered page as a base64-encoded PNG: full captures the entire"
        " scroll height, top captures the first viewport, and <width>x<height> sets the viewport size in"
        " pixels with an optional -<zoom> percent, such as 1280x800 or 1920x1080-75. Width and height range"
        " from 100 to 3840, zoom from 25 to 300. Setting it forces browser rendering. Leave it out for no"
        " screenshot."
    ),
    "get_screenshot": (
        "Screenshot area: full captures the entire scroll height (the default), top captures the first"
        " viewport, and <width>x<height> sets the viewport size in pixels with an optional -<zoom> percent,"
        " such as 1280x800 or 1920x1080-75. Width and height range from 100 to 3840, zoom from 25 to 300."
        " The image is a base64-encoded PNG."
    ),
    "scrape_json": (
        "Captures a screenshot of the rendered page as a base64-encoded image. Useful for verifying what the"
        " scraper actually saw. Off by default."
    ),
}
JSON_DESCRIPTIONS = {
    "prompt": (
        "Description of the data shape you want back, for example fields like title, price, and rating. The"
        " prompt only parses the scraped content; it cannot instruct the scraper itself. To shape the output"
        " with a JSON schema, append it to the prompt as a Json Schema: section. Defaults to \"Extract all"
        ' data as complete as possible."'
    ),
    "html": "Returns the raw HTML of the scraped page in the response. On by default; set false to leave it out.",
}
FUNCTIONS = {
    "status": ("Return the raw account status API response without summarizing, redacting body fields, or adding calculated fields.", {}),
    "scrape_html": (
        "Retrieve the raw HTML of a page. Browser rendering and a screenshot are optional.",
        {
            "url": inspect.Parameter.empty,
            "browser_rendering": None,
            "timeout": None,
            "warm_start": None,
            "block_resources": None,
            "wait_for_selector": None,
            "geo_code": None,
            "max_age": 0,
            "max_retries": None,
            "screenshot": None,
        },
    ),
    "scrape_markdown": (
        "Retrieve a page as Markdown. Browser rendering is optional.",
        {
            "url": inspect.Parameter.empty,
            "browser_rendering": None,
            "timeout": None,
            "geo_code": None,
            "warm_start": None,
            "block_resources": None,
            "wait_for_selector": None,
            "max_age": 0,
            "max_retries": None,
        },
    ),
    "get_screenshot": (
        (
            "Capture a base64 PNG screenshot of the full page, the top viewport, or a custom viewport size."
            " Browser rendering is always enabled."
        ),
        {
            "url": inspect.Parameter.empty,
            "screenshot": "full",
            "timeout": None,
            "geo_code": None,
            "wait_for_selector": None,
            "max_age": 0,
            "max_retries": None,
        },
    ),
    "scrape_brand": (
        "Extract a website's colors, typography, logos, and social links as JSON.",
        {"url": inspect.Parameter.empty},
    ),
    "scrape_json": (
        (
            "Extract structured JSON from a page using a natural-language prompt. Append Json Schema: and a"
            " JSON schema to shape the output."
        ),
        {
            "url": inspect.Parameter.empty,
            "prompt": None,
            "html": True,
            "screenshot": False,
            "browser_rendering": None,
            "wait_for_selector": None,
            "warm_start": None,
            "block_resources": None,
            "timeout": None,
            "geo_code": None,
            "max_age": 0,
            "max_retries": None,
        },
    ),
    "web_search": (
        "Search the web and return ranked results with titles, URLs, descriptions, and sources.",
        {"query": inspect.Parameter.empty, "country": None, "num_results": None},
    ),
}


@pytest.mark.parametrize("name", FUNCTIONS)
def test_signature_and_docstring_follow_the_spec(name):
    description, defaults = FUNCTIONS[name]
    method = getattr(mrscraper_tool.Tools, name)
    parameters = {
        key: parameter.default
        for key, parameter in inspect.signature(method).parameters.items()
        if key != "self" and not key.startswith("__")
    }
    assert parameters == defaults
    assert list(parameters) == list(defaults)
    assert not set(parameters) & set(REMOVED)
    lines = [line.strip() for line in method.__doc__.strip().splitlines()]
    assert lines[0] == description
    # Open WebUI 0.6.11 reads each ":param name: description" from a single line.
    documented = dict(re.match(r":param (\w+):\s*(.+)", line).groups() for line in lines[1:])
    assert list(documented) == list(defaults)
    expected = {**DESCRIPTIONS, **JSON_DESCRIPTIONS, "screenshot": SCREENSHOT_DESCRIPTIONS.get(name)}
    assert documented == {key: expected[key] for key in documented}


def test_only_the_seven_functions_are_public():
    public = [name for name in vars(mrscraper_tool.Tools) if not name.startswith("_") and name != "Valves"]
    assert public == list(FUNCTIONS)
