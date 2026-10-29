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

The server provides `scraperAPI`, `webSearch`, and `status`. See the
[MCP server documentation](https://docs.mrscraper.com/docs/getting-started/mcp-server).

## Option 2: Community tool

1. Open **Workspace → Tools** and click **+** (or import the tool from the
   Open WebUI community site).
2. Paste the contents of [`mrscraper_tool.py`](mrscraper_tool.py) and save.
   Open WebUI installs its dependency, `mrscraper-sdk`.
3. Open the tool's **Valves** and set `api_key` to your MrScraper API key.
4. Enable the tool in a chat or on a model.

| Function | What it does |
| --- | --- |
| `scrape_page` | Retrieves a public page as Markdown (default) or HTML. Long pages are cut at `max_characters` (Valve, default 100,000; 0 disables). |
| `web_search` | Searches the web and returns organic results (rank, title, link, description, source) and the next page number. |
| `account_status` | Shows the subscription status, token limit, tokens used, and tokens remaining. Uses no tokens. |

Errors are reported to the model as short messages with the HTTP status; the
API key never appears in them.

## Data and permissions

Target URLs, search queries, and request options are sent to MrScraper's API,
and `scrape_page` and `web_search` use tokens from your MrScraper plan. Use
them only for public or otherwise authorized content. See the
[Privacy Policy](https://mrscraper.com/privacy-policy) and
[Acceptable Use Policy](https://mrscraper.com/acceptable-use-policy).

Support: [support@mrscraper.com](mailto:support@mrscraper.com)

## License

[MIT](LICENSE)
