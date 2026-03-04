# Power BI Remote MCP Server

A read-only MCP server that connects your LLM to Power BI semantic models for
analysis purposes.  It uses the **Power BI REST API** with **OAuth 2.0 delegated
permissions** so every request runs under the authenticated user's own Power BI
access rights.

## Features

| Tool | Description |
|---|---|
| `authenticate` | OAuth device code flow login (one-time setup) |
| `list_workspaces` | List all workspaces the user belongs to |
| `list_datasets` | List datasets / semantic models in a workspace |
| `get_dataset_info` | Metadata + refresh history for a single dataset |
| `list_tables` | Visible tables in a dataset |
| `list_measures` | Measures with name, table, format, and DAX expression |
| `list_columns` | Columns / dimensions with data type and key flag |
| `execute_dax` | Execute any DAX query and return rows as JSON |

All dataset operations are **workspace-scoped** (`/groups/{workspaceId}/…`)
because datasets always live inside a Power BI workspace (group).

## Prerequisites

- Python 3.11+
- A Power BI Pro, Premium Per User (PPU), or Premium capacity licence
- An Azure AD **app registration** (public client / mobile & desktop)

### Azure AD App Registration

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active
   Directory** → **App registrations** → **New registration**.
2. Name it (e.g. `PowerBI MCP Server`), leave the default single-tenant or
   choose multi-tenant, click **Register**.
3. Under **Authentication** → **Platform configurations** add
   **Mobile and desktop applications** and tick the redirect URI:
   `https://login.microsoftonline.com/common/oauth2/nativeclient`
4. Under **API permissions** → **Add a permission** → **Power BI Service**:
   - `Dataset.Read.All` (Delegated)
   - `Workspace.Read.All` (Delegated)

   If your tenant requires admin consent, ask an admin to grant it.
5. Copy the **Application (client) ID** — you will need it below.

> **Note:** The tenant setting **"Dataset Execute Queries REST API"** must be
> enabled in the Power BI Admin Portal (Integration settings) for
> `execute_dax` to work.

## Installation

```bash
git clone https://github.com/mbrummerstedt/powerbi-remote-mcp-server.git
cd powerbi-remote-mcp-server

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
# Edit .env and set POWERBI_CLIENT_ID to your Azure AD app's client ID
```

## First-time login

Run the login helper once to cache your OAuth token:

```bash
python server.py --login
```

You will see a message like:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code ABCD1234 to authenticate.
```

Open the URL, enter the code, and sign in with your Power BI account.
The token is cached in `~/.powerbi_mcp_token_cache.json` and refreshes
automatically for ~90 days.

## Running the server

### stdio (for local MCP clients such as Claude Desktop)

```bash
python server.py
```

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "powerbi": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/powerbi-remote-mcp-server/server.py"],
      "env": {
        "POWERBI_CLIENT_ID": "your-client-id"
      }
    }
  }
}
```

## Typical analysis workflow

```
1. authenticate          (first run only)
2. list_workspaces       → find workspace_id
3. list_datasets         workspace_id=<id>  → find dataset_id
4. list_tables           workspace_id=<id>  dataset_id=<id>
5. list_measures         workspace_id=<id>  dataset_id=<id>  [table_name=<name>]
6. list_columns          workspace_id=<id>  dataset_id=<id>  [table_name=<name>]
7. execute_dax           workspace_id=<id>  dataset_id=<id>
                         dax_query="EVALUATE SUMMARIZECOLUMNS(...)"
```

## DAX query tips

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

## Limitations

- **Read-only**: creation, modification, and deletion of Power BI artefacts
  are not supported.
- DAX `execute_dax` limit: 100,000 rows / 1,000,000 values per query.
- Rate limit: 120 DAX query requests per minute per user.
- `list_tables`, `list_measures`, and `list_columns` use DAX `INFO.VIEW.*`
  functions which require Import or DirectQuery models with XMLA read access.

## Security

- Tokens are stored in `~/.powerbi_mcp_token_cache.json` (mode `600`).
- The server never stores or logs access tokens.
- All data access is gated by the user's own Power BI permissions.
