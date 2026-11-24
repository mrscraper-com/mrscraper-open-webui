# MrScraper for Open WebUI

Give [Open WebUI](https://openwebui.com) models live web data from
[MrScraper](https://mrscraper.com): retrieve public pages as Markdown or HTML,
search the web, and check your MrScraper plan and token usage.

There are two ways to connect. Use the MCP server if you are an Open WebUI
admin on v0.6.31 or later; use the community tool otherwise.

## Option 1: MrScraper MCP server (admins, Open WebUI 0.6.31+)

1. Open **Admin Settings → External Tools** and click **+**.
2. Set **Type** to **MCP (Streamable HTTP)**.
3. Set **Server URL** to `https://mcp.mrscraper.com/mcp`.
4. Choose an **Auth** method:
   - **Bearer**, with a MrScraper API key from
     [app.mrscraper.com/api-tokens](https://app.mrscraper.com/api-tokens) as the
     key; or
   - **OAuth 2.1**, which registers Open WebUI with MrScraper through dynamic
     client registration and asks each user to sign in to MrScraper.
5. Save, then enable the server for the models that should use it.

The server provides seven tools: `status`, `scrapeHtml`, `scrapeMarkdown`,
`getScreenshot`, `scrapeBrand`, `scrapeJson`, and `webSearch`. OAuth
connections need `scrape:read` for the six scraping and search tools and
`account:read` for `status`. See the
[MCP server documentation](https://docs.mrscraper.com/docs/getting-started/mcp-server).

## Option 2: Community tool (Open WebUI 0.6.11+)

1. Open **Workspace → Tools** and click **+** (or import the tool from the
   Open WebUI community site).
2. Paste the contents of [`mrscraper_tool.py`](mrscraper_tool.py) and save.
   It uses `httpx`, which Open WebUI already includes.
3. Open the tool's **Valves** and set `api_key` to your MrScraper API key.
4. Enable the tool in a chat or on a model.

The tool offers the seven MrScraper services with the same descriptions and
parameters as the MCP server (parameter names in snake_case). The parameters
follow the [API Reference](https://docs.mrscraper.com/docs/api/overview):

| Function | What it does |
| --- | --- |
| `status` | Return the raw account status API response without summarizing, redacting body fields, or adding calculated fields. |
| `scrape_html` | Retrieve the raw HTML of a page. Browser rendering and a screenshot are optional. |
| `scrape_markdown` | Retrieve a page as Markdown. Browser rendering is optional. |
| `get_screenshot` | Capture a base64 PNG screenshot of the full page, the top viewport, or a custom viewport size. Browser rendering is always enabled. |
| `scrape_brand` | Extract a website's colors, typography, logos, and social links as JSON. |
| `scrape_json` | Extract structured JSON from a page using a natural-language prompt. Append Json Schema: and a JSON schema to shape the output. |
| `web_search` | Search the web and return ranked results with titles, URLs, descriptions, and sources. |

Parameters (`status` takes none, and `scrape_brand` takes only `url`). A
parameter the model leaves out (or sets to null) without a default here is not
sent, so the API's own default applies:

| Parameter | Functions | Default | Description |
| --- | --- | --- | --- |
| `url` | all but `status` and `web_search` | Required | URL of the page to scrape. Must include the protocol (https://). |
| `browser_rendering` | `scrape_html`, `scrape_markdown`, `scrape_json` | Not sent | Controls whether the page is loaded in a browser. Leave it out to load the page without a browser first and switch to a browser automatically if the request is blocked. Set true to always load the page in a browser, which JavaScript-heavy sites, SPAs, and pages that load content dynamically need. Set false to never use a browser. |
| `timeout` | `scrape_html`, `scrape_markdown`, `get_screenshot`, `scrape_json` | Not sent | Maximum seconds to wait for the scrape before the request times out, from 1 to 330. Increase for slow or complex pages. Defaults to 300. |
| `warm_start` | `scrape_html`, `scrape_markdown`, `scrape_json` | Not sent | Opens the site's home page first to warm the session before loading the target URL. Useful for sites that expect a normal browsing session before deeper pages. |
| `block_resources` | `scrape_html`, `scrape_markdown`, `scrape_json` | Not sent | Blocks images, fonts, and stylesheets from loading. Speeds up scraping and reduces bandwidth usage. |
| `wait_for_selector` | `scrape_html`, `scrape_markdown`, `get_screenshot`, `scrape_json` | Not sent | Waits until a specific CSS selector or text appears on the page before returning it, such as #results. Up to 500 characters. Useful for content that loads asynchronously after the initial page load. |
| `geo_code` | same four | Not sent | Two-letter country code to load the page from, such as us or gb. Defaults to the United States. |
| `max_age` | same four | `0` | Serves a cached result of the same request when it is newer than this age, in milliseconds, such as 86400000 for 1 day. Up to 604800000 (7 days). 0, the default, always scrapes fresh. A cached result costs the same tokens as the original scrape. |
| `max_retries` | same four | Not sent | Maximum number of retries for the request. Omit for no limit. |
| `screenshot` | `scrape_html` | Not sent | Also captures a screenshot of the rendered page as a base64-encoded PNG: full captures the entire scroll height, top captures the first viewport, and <width>x<height> sets the viewport size in pixels with an optional -<zoom> percent, such as 1280x800 or 1920x1080-75. Width and height range from 100 to 3840, zoom from 25 to 300. Setting it forces browser rendering. Leave it out for no screenshot. |
| `screenshot` | `get_screenshot` | `"full"` | Screenshot area: full captures the entire scroll height (the default), top captures the first viewport, and <width>x<height> sets the viewport size in pixels with an optional -<zoom> percent, such as 1280x800 or 1920x1080-75. Width and height range from 100 to 3840, zoom from 25 to 300. The image is a base64-encoded PNG. |
| `screenshot` | `scrape_json` | `false` | Captures a screenshot of the rendered page as a base64-encoded image. Useful for verifying what the scraper actually saw. Off by default. |
| `html` | `scrape_json` | `true` | Returns the raw HTML of the scraped page in the response. On by default; set false to leave it out. |
| `prompt` | `scrape_json` | Not sent | Description of the data shape you want back, for example fields like title, price, and rating. The prompt only parses the scraped content; it cannot instruct the scraper itself. To shape the output with a JSON schema, append it to the prompt as a Json Schema: section. Defaults to "Extract all data as complete as possible." |
| `query` | `web_search` | Required | The search query, from 1 to 1000 characters. |
| `country` | `web_search` | Not sent | Two-letter country code to run the search from. Defaults to the United States. |
| `num_results` | `web_search` | Not sent | How many results to return, from 10 to 100. The search may return fewer. Defaults to 10. |

Every request of the four page functions also sends `saveResult=true`, and
each function sends its fixed values: `html=true&markdown=false` for
`scrape_html`, `markdown=true&html=false&screenshot=false` for
`scrape_markdown`, `browserRendering=true&html=false&markdown=false` for
`get_screenshot`, `agent=general` for `scrape_json`,
`action=brand-scraping&bundle=none` for `scrape_brand`, and `action=websearch`
for `web_search`. For example, `scrape_html` with only a `url` sends
`GET https://api.mrscraper.com/?url=https%3A%2F%2Fexample.com&maxAge=0&html=true&markdown=false&saveResult=true`.
Country codes are sent in lowercase. `scrape_json` switches to a POST (with
`url`, `prompt`, and `agent` in the body) when its URL would exceed 8,000
characters. The page functions wait for `timeout` (300 seconds when not set)
plus 30 seconds; `scrape_brand` and `web_search` wait 120 seconds.

Each function returns the response envelope as JSON text: `status_code`,
`data` (the API response body), `headers` (credential headers removed), and
`error` when the request fails (`status_code` is null for network failures and
timeouts). Scraping and search responses have credential fields redacted;
`status` returns the account response body unchanged. Invalid inputs and a
missing API key are reported before any request is sent.

Results longer than `max_characters` (Valve, default 100,000; 0 disables) are
cut, with a note. Screenshots are base64 text, so raise or disable the limit to
receive them whole.

## Data and permissions

Target URLs, search queries, and request options are sent to MrScraper's API,
and the six scraping and search functions use tokens from your MrScraper plan.
Results of the four page functions are also saved to your MrScraper dashboard.
Use them only for public or otherwise authorized content. See the
[Privacy Policy](https://mrscraper.com/privacy-policy) and
[Acceptable Use Policy](https://mrscraper.com/acceptable-use-policy).

Support: [support@mrscraper.com](mailto:support@mrscraper.com)

## Development

The tests stub HTTP and make no API calls:

```bash
uv run --with httpx --with pydantic --with pytest pytest -q
```

## License

[MIT](LICENSE)
