# Power BI Remote MCP Server

A read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects an LLM (Claude, Cursor, etc.) to your Power BI semantic models. Every request runs under your own Power BI account via **OAuth 2.0 delegated permissions** — the server never has credentials of its own.

---

## Tools

| Tool | Description |
|---|---|
| `authenticate` | OAuth device code flow login — opens a browser URL you approve once |
| `list_workspaces` | List workspaces the user belongs to |
| `list_datasets` | List datasets / semantic models in a workspace |
| `get_dataset_info` | Metadata + last 5 refresh history entries for a dataset |
| `list_tables` | Visible tables in a dataset |
| `list_measures` | Measures with name, table, format string, and DAX expression |
| `list_columns` | Columns / dimensions with data type and key flag |
| `execute_dax` | Execute any DAX query and return rows as JSON |

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

```bash
git clone https://github.com/mbrummerstedt/powerbi-remote-mcp-server.git
cd powerbi-remote-mcp-server

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
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
```

### Step 4 — Log in (one-time)

```bash
python server.py --login
```

You will see a message like:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code ABCD1234 to authenticate.
```

Open the URL, enter the code, and sign in with your Power BI account. The token is cached securely in `~/.powerbi_mcp_token_cache.bin` using your OS's native secure storage (Keychain on macOS, DPAPI on Windows, LibSecret on Linux). It refreshes automatically for approximately 90 days.

---

### Step 5 — Connect to your MCP client

#### Claude Desktop

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "powerbi": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/powerbi-remote-mcp-server/server.py"],
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
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/powerbi-remote-mcp-server/server.py"],
      "env": {
        "POWERBI_CLIENT_ID": "your-client-id"
      }
    }
  }
}
```

---

### Typical analysis workflow

Once connected, ask your LLM to follow this sequence naturally:

```
1. authenticate          ← first run only; approves browser prompt
2. list_workspaces       → returns workspace IDs
3. list_datasets         workspace_id=<id>   → returns dataset IDs
4. list_tables           workspace_id=<id>   dataset_id=<id>
5. list_measures         workspace_id=<id>   dataset_id=<id>   [table_name=<name>]
6. list_columns          workspace_id=<id>   dataset_id=<id>   [table_name=<name>]
7. execute_dax           workspace_id=<id>   dataset_id=<id>
                         dax_query="EVALUATE SUMMARIZECOLUMNS(...)"
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

## Developer guide

### Project structure

```
powerbi-remote-mcp-server/
├── server.py                   # Entry point: loads config, registers tools, handles --login
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Test dependencies (pytest, respx)
├── pytest.ini                  # asyncio_mode = auto
├── .env.example                # Environment variable template
│
├── powerbi_mcp/
│   ├── __init__.py
│   ├── config.py               # Pydantic BaseSettings (POWERBI_CLIENT_ID, POWERBI_TENANT_ID)
│   ├── auth.py                 # MSAL device code flow + OS-native secure token cache
│   ├── client.py               # Async httpx wrapper around the Power BI REST API
│   ├── models.py               # Pydantic response models (Workspace, Dataset, etc.)
│   └── tools.py                # All @mcp.tool() registrations
│
├── tests/
│   ├── conftest.py             # Shared fixtures and mock API payloads
│   ├── test_models.py          # Pydantic model validation unit tests
│   ├── test_client.py          # PowerBIClient tests with respx HTTP mocking
│   ├── test_tools.py           # Full-stack MCP tool tests (mock HTTP + auth patch)
│   └── integration/
│       └── test_live_api.py    # Real API calls — auto-skipped if no cached token
│
└── .github/workflows/tests.yml # CI: runs mock test suite on every push / PR
```

### Set up a development environment

```bash
git clone https://github.com/mbrummerstedt/powerbi-remote-mcp-server.git
cd powerbi-remote-mcp-server

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
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
server.py
  └── Settings (pydantic-settings)   ← reads POWERBI_CLIENT_ID / POWERBI_TENANT_ID
  └── FastMCP instance
  └── register_tools(mcp, client_id, tenant_id)
        └── PowerBIAuth               ← MSAL PublicClientApplication + PersistedTokenCache
        └── @mcp.tool() functions
              └── PowerBIClient(token) ← httpx async client
                    └── Pydantic models (Workspace, Dataset, …)
```

**Key design decisions:**

- **No service principal.** Authentication uses the device code flow (delegated OAuth 2.0), so data access is always scoped to the signed-in user's own Power BI permissions.
- **OS-native token storage.** `msal-extensions` persists the token cache using the platform's secure store (Keychain / DPAPI / LibSecret) rather than a plain file.
- **Pydantic throughout.** Settings are validated at startup; all API responses are parsed into typed Pydantic models before being handled by tools.
- **Read-only by design.** The two OAuth scopes (`Dataset.Read.All`, `Workspace.Read.All`) and the tool set only allow reads.

### Adding a new tool

1. Add a method to `PowerBIClient` in `powerbi_mcp/client.py`.
2. If the response shape is new, add a Pydantic model in `powerbi_mcp/models.py`.
3. Register a `@mcp.tool()` function in `powerbi_mcp/tools.py` inside `register_tools`.
4. Add tests in `tests/test_tools.py` (mock) and optionally `tests/integration/test_live_api.py`.

---

## Limitations

- **Read-only.** Creation, modification, and deletion of Power BI artefacts are not supported.
- DAX `execute_dax` limits: 100,000 rows or 1,000,000 values per query.
- Rate limit: 120 DAX query requests per minute per user.
- `list_tables`, `list_measures`, and `list_columns` use the DAX `INFO.VIEW.*` functions, which require Import or DirectQuery models with XMLA read access enabled.

---

## Security

- Tokens are persisted using OS-native secure storage via [`msal-extensions`](https://github.com/AzureAD/microsoft-authentication-extensions-for-python):
  - **macOS** — Keychain
  - **Windows** — DPAPI-encrypted file
  - **Linux** — LibSecret (gnome-keyring / KWallet); falls back to an encrypted file if LibSecret is unavailable
- The cache file is written to `~/.powerbi_mcp_token_cache.bin` and is covered by `.gitignore`.
- The server never logs access tokens.
- All data access is gated by the user's own Power BI permissions (delegated OAuth 2.0 — no service principal, no client secret).
