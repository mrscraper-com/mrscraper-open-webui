# Changelog

Changes to the MrScraper community tool (`mrscraper_tool.py`). The project
follows [Semantic Versioning](https://semver.org/).

## 0.4.0 - 2026-11-24

- Parameters follow MrScraper's API reference and integration parameter spec,
  like MrScraper MCP server 0.6.0. The `scrape_html`, `scrape_markdown`, and
  `get_screenshot` descriptions and every parameter description use the new
  text.
- Removed parameters: `super`, `proxy`, `proxy_country`, `wait_until`,
  `return_cookie`, and `save_result` on the page functions, `html` on
  `scrape_markdown`, and `html` and `markdown` on `get_screenshot`.
- Optional parameters default to None and are sent only when the model sets
  them, so the API's defaults apply: `browser_rendering`, `timeout`,
  `warm_start`, `block_resources`, `wait_for_selector`, `geo_code`,
  `max_retries`, `prompt`, `country`, and `num_results`. 0.3.0 always sent
  `timeout=300`, `geoCode=us`, `proxyCountry=us`, `super=false`,
  `waitUntil=domcontentloaded`, `browserRendering=false`, and more.
- The four page functions always send `saveResult=true` and send `maxAge=0`
  unless `max_age` (new, 0 to 604800000 ms) is set. `timeout` accepts 1 to 330.
- `scrape_html` takes `screenshot` (`full`, `top`, or `<width>x<height>` with an
  optional `-<zoom>`). `get_screenshot` accepts the same custom sizes and no
  longer asks for HTML; `scrape_markdown` no longer asks for HTML.
  `scrape_json` takes `wait_for_selector`, `warm_start`, and
  `block_resources`, and `html` defaults to true.
- Minimum Open WebUI version stays 0.6.11: earlier versions cannot load tools
  with optional parameters.
- Added stubbed tests (`tests/`) that pin the exact request of each function.
