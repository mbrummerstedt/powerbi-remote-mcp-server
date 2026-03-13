# Power BI Analyst MCP

A read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects an LLM (Claude, Cursor, etc.) to your Power BI semantic models. Every request runs under your own Power BI account via **OAuth 2.0 delegated permissions** — the server never has credentials of its own.

---

## Tools

| Tool | Description |
|---|---|
| `authenticate` | OAuth device code flow — first call returns a URL + code; second call completes sign-in |
| `logout` | Clear cached credentials (forces re-authentication on next call) |
| `list_workspaces` | List workspaces the user belongs to |
| `list_datasets` | List datasets / semantic models in a workspace |
| `get_dataset_info` | Metadata + last 5 refresh history entries for a dataset |
| `list_tables` | Visible tables in a dataset |
| `list_measures` | Measures with name, table, description, and format string |
| `list_columns` | Columns / dimensions with data type and key flag |
| `execute_dax` | Execute any DAX query; small results returned inline, large results saved to CSV |
| `read_query_result` | Page through a large CSV result saved by `execute_dax` |

---

## User guide

### Prerequisites

- Python 3.11+
- A Power BI Pro, Premium Per User (PPU), or Premium capacity licence
- An Azure AD app registration (free, takes ~5 minutes — see below)

### Step 1 — Create an Azure AD app registration

OAuth 2.0 requires a **client ID** to identify which application is acting on your behalf. The registration is free, requires no client secret, and needs no Power BI admin consent for the two read-only scopes used here.

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**.
2. Give it a name (e.g. `PowerBI MCP Server`). For supported account types, choose **Accounts in this organizational directory only** (single-tenant) or **Accounts in any organizational directory** (multi-tenant). Click **Register**.
3. Under **Authentication** → **Platform configurations**, add **Mobile and desktop applications** and tick the redirect URI:
   ```
   https://login.microsoftonline.com/common/oauth2/nativeclient
   ```
4. Still under **Authentication** → **Advanced settings**, set **Allow public client flows** to **Yes**. Save.
5. Under **API permissions** → **Add a permission** → **Power BI Service**, add these two delegated permissions:
   - `Dataset.Read.All`
   - `Workspace.Read.All`

   If your tenant requires admin consent, ask an admin to grant it.
6. Copy the **Application (client) ID** from the Overview page — you will need it in the next step.

> **Tip:** If your app registration is single-tenant, you also need your **Directory (tenant) ID** from the same Overview page. Multi-tenant registrations can leave `POWERBI_TENANT_ID` at its default (`organizations`).

> **Note:** The Power BI tenant setting **"Dataset Execute Queries REST API"** must be enabled in the Power BI Admin Portal (Integration settings) for `execute_dax` to work.

---

### Step 2 — Install

**Recommended — install with `uv` (no venv management needed):**

```bash
pip install uv   # if you don't have uv yet
```

**Alternative — install with pip:**

```bash
pip install powerbi-analyst-mcp
```

**Manual — clone and install from source:**

```bash
git clone https://github.com/mbrummerstedt/powerbi-analyst-mcp.git
cd powerbi-analyst-mcp
pip install .
```

### Step 3 — Configure

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```dotenv
POWERBI_CLIENT_ID=your-application-client-id-here

# Only needed if your app is single-tenant. Otherwise leave as "organizations".
POWERBI_TENANT_ID=organizations

# Directory where large DAX query results are saved as CSV files (default: "powerbi_output").
# Can be an absolute path or relative to the server's working directory.
POWERBI_OUTPUT_DIR=powerbi_output
```

### Step 4 — Connect to your MCP client

#### Claude Desktop

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "powerbi": {
      "command": "uvx",
      "args": ["powerbi-analyst-mcp"],
      "env": {
        "POWERBI_CLIENT_ID": "your-client-id",
        "POWERBI_TENANT_ID": "organizations"
      }
    }
  }
}
```

The `claude_desktop_config.json` is located at:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

#### Cursor

Add a `.cursor/mcp.json` file in your project (or use the global config):

```json
{
  "mcpServers": {
    "powerbi": {
      "command": "uvx",
      "args": ["powerbi-analyst-mcp"],
      "env": {
        "POWERBI_CLIENT_ID": "your-client-id"
      }
    }
  }
}
```

#### Alternative (without uv)

If you installed via `pip install powerbi-analyst-mcp`, replace `"command": "uvx"` + `"args": ["powerbi-analyst-mcp"]` with:


```json
"command": "powerbi-analyst-mcp",
"args": []
```

Or point directly at the installed script path.

---

### Typical analysis workflow

Once connected, ask your LLM to follow this sequence naturally:

```
1. authenticate          ← first run only; returns a URL + code to open in browser,
                           then call authenticate again to complete sign-in
2. list_workspaces       → returns workspace IDs
3. list_datasets         workspace_id=<id>   → returns dataset IDs
4. list_tables           workspace_id=<id>   dataset_id=<id>
5. list_measures         workspace_id=<id>   dataset_id=<id>   [table_name=<name>]
6. list_columns          workspace_id=<id>   dataset_id=<id>   [table_name=<name>]
7. execute_dax           workspace_id=<id>   dataset_id=<id>
                         dax_query="EVALUATE SUMMARIZECOLUMNS(...)"
                         [result_name="my descriptive name"]   ← names the saved CSV
                         [max_rows=500]                        ← optional row cap
8. read_query_result     file_path=<savedTo>   [offset=0]   [limit=100]
                         ← page through large results without filling context
```

### DAX query examples

```dax
-- Total sales by year
EVALUATE
SUMMARIZECOLUMNS(
    'Date'[Year],
    "Total Sales", [Total Sales]
)
ORDER BY 'Date'[Year]

-- Top 10 customers by revenue
EVALUATE
TOPN(
    10,
    SUMMARIZECOLUMNS('Customer'[Name], "Revenue", [Revenue]),
    [Revenue], DESC
)

-- Filtered subset
EVALUATE
CALCULATETABLE(
    'Sales',
    'Date'[Year] = 2024
)
```

---

### Handling large results

Power BI queries can return up to 100,000 rows. Returning all of that inline would consume most of an LLM's context window. The server handles this automatically:

| Result size | Behaviour |
|---|---|
| **≤ 50 rows** | Returned inline as JSON — zero friction, just like a normal tool call |
| **> 50 rows** | Full result saved to a timestamped CSV; a compact summary is returned instead |

The summary contains `rowCount`, `columns`, a 5-row `preview`, and `savedTo` (the absolute path to the CSV). From there the agent can either read the file directly or page through it with `read_query_result`.

**`execute_dax` parameters for large-result control:**

| Parameter | Type | Description |
|---|---|---|
| `result_name` | `str` (optional) | Short label used in the CSV filename — e.g. `"gmv by market 2024"` → `dax_result_gmv_by_market_2024_20260305_143022.csv`. Sanitised to a safe slug, max 40 characters. |
| `max_rows` | `int` (optional) | Hard cap applied at the Power BI engine level via `TOPN`. Useful for quick sampling without rewriting the DAX. |

**Paging through a saved CSV with `read_query_result`:**

```
read_query_result(
    file_path = "/path/from/savedTo",
    offset    = 0,      # zero-based row offset
    limit     = 100     # rows per page (default 100)
)
```

Returns `rows`, `totalRows`, `offset`, `limit`, and `hasMore`. Increment `offset` by `limit` to fetch the next page.

**Output directory** defaults to `powerbi_output/` relative to the server's working directory. Override with `POWERBI_OUTPUT_DIR` in your `.env`.

---

## Developer guide

### Project structure

```
powerbi-analyst-mcp/
├── pyproject.toml              # Package metadata and entry point (powerbi-analyst-mcp)
├── server.py                   # CLI wrapper: handles --login flag for terminal auth
├── requirements.txt            # Runtime dependencies (mirrors pyproject.toml)
├── requirements-dev.txt        # Test dependencies (pytest, respx)
├── pytest.ini                  # asyncio_mode = auto
├── .env.example                # Environment variable template
│
├── powerbi_mcp/
│   ├── __init__.py
│   ├── __main__.py             # Enables: python -m powerbi_mcp
│   ├── app.py                  # FastMCP instance, settings, main() entry point
│   ├── config.py               # Pydantic BaseSettings (POWERBI_CLIENT_ID, POWERBI_TENANT_ID, POWERBI_OUTPUT_DIR)
│   ├── auth.py                 # MSAL device code flow + OS-native secure token cache
│   ├── client.py               # Async httpx wrapper around the Power BI REST API
│   ├── models.py               # Pydantic response models (Workspace, Dataset, etc.)
│   ├── output.py               # CSV helpers: save_rows_to_csv, read_csv_page
│   └── tools.py                # All @mcp.tool() registrations
│
├── tests/
│   ├── conftest.py             # Shared fixtures and mock API payloads
│   ├── test_models.py          # Pydantic model validation unit tests
│   ├── test_client.py          # PowerBIClient tests with respx HTTP mocking
│   ├── test_output.py          # CSV save/read helper unit tests
│   ├── test_tools.py           # Full-stack MCP tool tests (mock HTTP + auth patch)
│   └── integration/
│       └── test_live_api.py    # Real API calls — auto-skipped if no cached token
│
└── .github/workflows/tests.yml # CI: runs mock test suite on every push / PR
```

### Set up a development environment

```bash
git clone https://github.com/mbrummerstedt/powerbi-analyst-mcp.git
cd powerbi-analyst-mcp

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
# or: pip install -r requirements.txt -r requirements-dev.txt
```

If you use [direnv](https://direnv.net/), the `.envrc` file activates the virtual environment automatically when you `cd` into the project.

### Running the tests

**Mock tests** (no credentials needed — safe for CI):

```bash
pytest tests/ -v
```

**Integration tests** (requires a cached login token — run `python server.py --login` first):

```bash
pytest tests/integration/ -v
```

The integration tests call the real Power BI REST API and skip automatically if no token is cached, so they never block CI.

### Architecture overview

```
powerbi_mcp/app.py
  └── Settings (pydantic-settings)   ← reads POWERBI_CLIENT_ID / POWERBI_TENANT_ID / POWERBI_OUTPUT_DIR
  └── FastMCP instance
  └── register_tools(mcp, client_id, tenant_id, output_dir)
        └── PowerBIAuth               ← MSAL PublicClientApplication + PersistedTokenCache
        └── @mcp.tool() functions
              └── PowerBIClient(token) ← httpx async client
                    └── Pydantic models (Workspace, Dataset, …)
              └── save_rows_to_csv / read_csv_page  ← output.py (large result handling)
```

**Key design decisions:**

- **No service principal.** Authentication uses the device code flow (delegated OAuth 2.0), so data access is always scoped to the signed-in user's own Power BI permissions.
- **OS-native token storage.** `msal-extensions` persists the token cache using the platform's secure store (Keychain / DPAPI / LibSecret) rather than a plain file.
- **Pydantic throughout.** Settings are validated at startup; all API responses are parsed into typed Pydantic models before being handled by tools.
- **Read-only by design.** The two OAuth scopes (`Dataset.Read.All`, `Workspace.Read.All`) and the tool set only allow reads.
- **Context-window-safe results.** `execute_dax` returns small results (≤ 50 rows) inline and automatically writes larger results to a named CSV file, keeping the agent context lean regardless of query size.

### Adding a new tool

1. Add a method to `PowerBIClient` in `powerbi_mcp/client.py`.
2. If the response shape is new, add a Pydantic model in `powerbi_mcp/models.py`.
3. Register a `@mcp.tool()` function in `powerbi_mcp/tools.py` inside `register_tools`.
4. Add tests in `tests/test_tools.py` (mock) and optionally `tests/integration/test_live_api.py`.

---

## Limitations

- **Read-only.** Creation, modification, and deletion of Power BI artefacts are not supported.
- DAX `execute_dax` limits: 100,000 rows or 1,000,000 values per query (Power BI API hard cap).
- Rate limit: 120 DAX query requests per minute per user.
- `list_tables`, `list_measures`, and `list_columns` use the DAX `INFO.VIEW.*` functions, which require Import or DirectQuery models with XMLA read access enabled.
- CSV files written by `execute_dax` are not automatically cleaned up. Manage the `POWERBI_OUTPUT_DIR` directory manually or add your own retention policy.

---

## Security

- Tokens are persisted using OS-native secure storage via [`msal-extensions`](https://github.com/AzureAD/microsoft-authentication-extensions-for-python):
  - **macOS** — Keychain
  - **Windows** — DPAPI-encrypted file
  - **Linux** — LibSecret (gnome-keyring / KWallet); falls back to an encrypted file if LibSecret is unavailable
- The cache file is written to `~/.powerbi_mcp_token_cache.bin` and is covered by `.gitignore`.
- The server never logs access tokens.
- All data access is gated by the user's own Power BI permissions (delegated OAuth 2.0 — no service principal, no client secret).
