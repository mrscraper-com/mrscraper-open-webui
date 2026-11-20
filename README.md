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
connections made before MCP server 0.5.0 must sign in again and approve
`scraper:read`. See the
[MCP server documentation](https://docs.mrscraper.com/docs/getting-started/mcp-server).

## Option 2: Community tool (Open WebUI 0.6.11+)

1. Open **Workspace → Tools** and click **+** (or import the tool from the
   Open WebUI community site).
2. Paste the contents of [`mrscraper_tool.py`](mrscraper_tool.py) and save.
   It uses `httpx`, which Open WebUI already includes.
3. Open the tool's **Valves** and set `api_key` to your MrScraper API key.
4. Enable the tool in a chat or on a model.

The tool offers the seven MrScraper services with the same descriptions,
parameters, and defaults as the MCP server and the Python SDK:

| Function | What it does |
| --- | --- |
| `status` | Return the raw account status API response without summarizing, redacting body fields, or adding calculated fields. |
| `scrape_html` | Retrieve the raw HTML of a page. Browser rendering is optional. |
| `scrape_markdown` | Retrieve a page as Markdown. Including HTML also enables browser rendering and returns both outputs as JSON. |
| `get_screenshot` | Capture a base64 screenshot of the full page or top viewport. Browser rendering is always enabled. |
| `scrape_brand` | Extract a website's colors, typography, logos, and social links as JSON. |
| `scrape_json` | Extract structured JSON from a page using a natural-language prompt. Append Json Schema: and a JSON schema to shape the output. |
| `web_search` | Search the web and return ranked results with titles, URLs, descriptions, and sources. |

Parameters (`status` takes none, and `scrape_brand` takes only `url`):

| Parameter | Functions | Default | Description |
| --- | --- | --- | --- |
| `url` | all but `status` and `web_search` | Required | Absolute HTTP or HTTPS URL of the page to process. |
| `timeout` | `scrape_html`, `scrape_markdown`, `get_screenshot`, `scrape_json` | `300` | Page load timeout in seconds, from 1 to 300. The HTTP request allows 30 additional seconds. |
| `geo_code` | same four | `"us"` | Two-letter country code to load the page from, sent in lowercase. |
| `proxy_country` | same four | `"us"` | Two-letter country code of the proxy server, sent in lowercase. |
| `super` | same four | `false` | Whether to route the request through real devices. |
| `wait_until` | same four | `"domcontentloaded"` | Navigation completion event to wait for: domcontentloaded, load, networkidle0, or networkidle2. |
| `proxy` | same four | Omitted | Custom proxy URL used to load the page. |
| `max_retries` | same four | Omitted | Maximum number of retries, as a nonnegative integer. Omit this parameter for no limit. |
| `save_result` | same four | `false` | Whether to save the result to the dashboard Result tab. |
| `block_resources` | `scrape_html`, `scrape_markdown`, `get_screenshot` | `false` | Whether to block images, fonts, and stylesheets while loading the page. |
| `wait_for_selector` | same three | Omitted | Wait for the page to contain the specified CSS selector before returning the result. |
| `return_cookie` | same three | `false` | Whether to include browser session cookies in the response. |
| `browser_rendering` | `scrape_html`, `scrape_markdown`, `scrape_json` | `false` | Whether to render the page in a browser before returning the result. |
| `warm_start` | `scrape_html`, `scrape_markdown` | `false` | Whether to enable warm start for more reliable scraping, at the cost of additional latency. |
| `html` | `scrape_markdown`, `scrape_json` | `false` | Whether to include the page HTML in the response. |
| `html` | `get_screenshot` | `true` | Whether to include the page HTML in the response. |
| `markdown` | `get_screenshot` | `false` | Whether to include the page Markdown in the response. |
| `screenshot` | `get_screenshot` | `"full"` | Screenshot area to capture: full for the entire page or top for the top viewport. |
| `screenshot` | `scrape_json` | `false` | Whether to include a base64 screenshot in the response. |
| `prompt` | `scrape_json` | `"Extract all data as complete as possible."` | Instructions describing the data to extract. Append Json Schema: and a JSON schema to shape the output. |
| `query` | `web_search` | Required | Search query text, trimmed before sending, containing 1 to 1000 characters. |
| `country` | `web_search` | `"us"` | Two-letter country code to search from, sent in lowercase. |
| `num_results` | `web_search` | `10` | Number of search results to request, as an integer from 10 to 100. Fewer results may be returned. |

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
With `save_result`, results are also saved to your MrScraper dashboard. Use
them only for public or otherwise authorized content. See the
[Privacy Policy](https://mrscraper.com/privacy-policy) and
[Acceptable Use Policy](https://mrscraper.com/acceptable-use-policy).

Support: [support@mrscraper.com](mailto:support@mrscraper.com)

## License

[MIT](LICENSE)
