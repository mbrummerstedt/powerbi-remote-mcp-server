# Power BI Analyst MCP

<!-- mcp-name: io.github.mbrummerstedt/powerbi-analyst-mcp -->

[![PyPI](https://img.shields.io/pypi/v/powerbi-analyst-mcp)](https://pypi.org/project/powerbi-analyst-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/powerbi-analyst-mcp)](https://pypi.org/project/powerbi-analyst-mcp/)
[![Tests](https://img.shields.io/github/actions/workflow/status/mbrummerstedt/powerbi-analyst-mcp/tests.yml?label=tests)](https://github.com/mbrummerstedt/powerbi-analyst-mcp/actions)
[![License: MIT](https://img.shields.io/github/license/mbrummerstedt/powerbi-analyst-mcp)](LICENSE)

**Ask Claude to analyse your Power BI data. Get answers — not context-window crashes.**

Connect Claude (or any MCP client) to your Power BI semantic models. Explore tables and measures, run DAX queries, and work with real results — even across datasets with tens of thousands of rows. Large query results are automatically saved to a local file and paged to the agent on demand, so your AI session stays fast and focused no matter how much data you pull.

Everything runs on your machine. Your data never passes through a third-party relay.

---

## What becomes possible

The analytics bottleneck has never really been data access — Power BI already gives people access. The bottleneck is translation: the skilled, time-consuming work of turning data into a decision. This server moves that translation to an LLM.

**Compound reasoning across your entire model**
A human analyst runs one query, reads the result, forms a hypothesis, runs another query. This serialises over hours. An agent can run twenty queries in sequence — each informed by the last — synthesise across all of them, and deliver a reasoned conclusion in minutes. Ask "what's driving the margin decline in EMEA?" and Claude will explore measures, drill into markets, check time trends, isolate the outlier, and explain it — without you directing each step.

**Natural language analytics for everyone**
Any stakeholder can ask a data question and get a real answer backed by live DAX — without knowing what DAX is, without filing a ticket, without waiting. The translation layer that used to require a trained analyst runs on demand.

**Proactive anomaly detection**
Run Claude on a schedule against your key measures. It queries the data, compares to prior periods, and flags anything outside expected ranges in plain English — before anyone has to open a dashboard to find out something went wrong.

**Self-documenting semantic models**
"List every measure in this dataset and explain what it calculates." Claude explores the schema and produces a data dictionary — useful for onboarding, governance, and anyone trying to understand what a model actually contains.

**Large datasets, handled automatically**
Power BI queries can return tens of thousands of rows. Returning all of that inline would consume most of an LLM's context window and crash the session. This server saves large results to a local CSV and gives the agent a compact summary — row count, column names, 5-row preview — then lets it page through the file on demand. You get the full dataset. The AI session stays lean.

**Query history that compounds over time**
Every successful DAX query is logged locally in a JSONL audit trail — what the user asked, the DAX that was generated, the columns returned, and where the CSV was saved. In the next session, the agent searches this history to find relevant prior work: reusable DAX patterns, previously computed result files, and context from earlier analyses. The more you use it, the faster and smarter it gets — and you always have an audit trail of where every number came from.

**Your data stays on your machine**
Queries, results, and tokens never pass through a cloud relay. The server runs locally, authenticates via the same OAuth device code flow as the Power BI web app, and stores tokens in your OS's native secure store (Keychain / DPAPI / LibSecret).

---

## How it compares to Microsoft's official MCP servers

Microsoft publishes two MCP servers for Power BI. This one is different in purpose and architecture from both.

|  | **This server** | [Microsoft Remote MCP](https://learn.microsoft.com/en-us/power-bi/developer/mcp/mcp-servers-overview#remote-power-bi-mcp-server) | [Microsoft Modeling MCP](https://learn.microsoft.com/en-us/power-bi/developer/mcp/mcp-servers-overview#power-bi-modeling-mcp-server) |
|---|---|---|---|
| **Purpose** | Query and analyse existing models | Query existing models | Build and modify models |
| **Runs** | Locally on your machine | Microsoft's cloud infrastructure | Locally (Power BI Desktop) |
| **Data path** | Direct to Power BI REST API | Via Microsoft's MCP relay | Local XMLA |
| **Auth** | OAuth device code (delegated) | OAuth via remote service | Local session |
| **Large results** | Auto-saved to local CSV | In-context only | N/A |
| **Read-only** | Yes | Yes | No |
| **Who it's for** | Analysts using Claude / Cursor | Analysts using Copilot | Model developers |

---

## What it looks like

```
You: Analyse revenue by market and product category for Q1 2025

Claude: Let me explore the dataset first.
        [calls list_tables → list_measures → execute_dax]

        The query returned 73,840 rows — saved to:
        ~/powerbi_output/dax_result_revenue_q1_2025_20260313_091204.csv

        [pages through results with read_query_result]

        Summary: EMEA leads at 44% of total revenue. The top category
        is Premium Hardware in both EMEA and AMER. APAC shows the
        strongest quarter-over-quarter growth at +18%...
```

The agent explores the schema, writes the DAX, handles the file, and delivers the analysis — without you touching the Power BI UI.

---

## Tools

| Tool | What it does |
|---|---|
| `authenticate` | Sign in via OAuth device code — returns a URL + one-time code; call again to complete |
| `logout` | Clear the cached token (forces re-authentication) |
| `list_workspaces` | List all workspaces you have access to |
| `list_datasets` | List datasets / semantic models in a workspace |
| `get_dataset_info` | Metadata and last 5 refresh history entries for a dataset |
| `list_tables` | All visible tables in a dataset |
| `list_measures` | Measures with name, table, description, and format string |
| `list_columns` | Columns with data type and key flag |
| `execute_dax` | Run a DAX query — inline for small results, local CSV for large ones. Pass `query_summary` to log the query for future reference. |
| `read_query_result` | Page through a large CSV result without loading it all into context |
| `search_query_history` | Search the local query log by keyword, dataset, or time range — find prior DAX and results across sessions |
| `delete_query_log_entry` | Remove a query log entry (e.g. when the approach turned out to be wrong) |

---

## User guide

### Prerequisites

- Python 3.11+
- A Power BI Pro, Premium Per User (PPU), or Premium capacity licence
- An Azure AD app registration (free, ~5 minutes — see below)

### Step 1 — Create an Azure AD app registration

OAuth 2.0 requires a **client ID** to identify which application is acting on your behalf. The registration is free, requires no client secret, and does not need Power BI admin consent for the two read-only scopes used here.

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active Directory** → **App registrations** → **New registration**.
2. Name it (e.g. `PowerBI MCP`). For account types choose **Accounts in this organizational directory only** (single-tenant). Click **Register**.
3. Under **Authentication** → **Platform configurations**, add **Mobile and desktop applications** and tick this redirect URI:
   ```
   https://login.microsoftonline.com/common/oauth2/nativeclient
   ```
4. Still under **Authentication** → **Advanced settings**, set **Allow public client flows** to **Yes**. Save.
5. Under **API permissions** → **Add a permission** → **Power BI Service**, add:
   - `Dataset.Read.All`
   - `Workspace.Read.All`

   If your tenant requires admin consent, ask an admin to grant it.

6. From the **Overview** page, copy both of these — you will need them in Step 2:
   - **Application (client) ID**
   - **Directory (tenant) ID**

> **Note:** The Power BI tenant setting **"Dataset Execute Queries REST API"** must be enabled in the Power BI Admin Portal (Integration settings) for `execute_dax` to work.

---

### Step 2 — Install and connect

The server is published on PyPI. The fastest way to run it is with `uvx`, which requires no manual install or virtual environment.

#### Claude Desktop

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "powerbi": {
      "command": "uvx",
      "args": ["powerbi-analyst-mcp"],
      "env": {
        "POWERBI_CLIENT_ID": "your-application-client-id",
        "POWERBI_TENANT_ID": "your-directory-tenant-id"
      }
    }
  }
}
```

The config file is at:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

> **`POWERBI_TENANT_ID` is required for almost all users.** Most organisations have a single Azure AD tenant. Set this to your **Directory (tenant) ID** from Step 1. Leaving it as `organizations` (the default) will cause authentication to fail or target the wrong tenant.

#### Cursor

Add a `.cursor/mcp.json` file in your project (or use the global config):

```json
{
  "mcpServers": {
    "powerbi": {
      "command": "uvx",
      "args": ["powerbi-analyst-mcp"],
      "env": {
        "POWERBI_CLIENT_ID": "your-application-client-id",
        "POWERBI_TENANT_ID": "your-directory-tenant-id"
      }
    }
  }
}
```

#### pip install (alternative)

```bash
pip install powerbi-analyst-mcp
```

Then replace the `uvx` command block with:

```json
"command": "powerbi-analyst-mcp",
"args": []
```

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
7. search_query_history  [keyword="revenue"]  ← check if similar work exists from a prior session
8. execute_dax           workspace_id=<id>   dataset_id=<id>
                         dax_query="EVALUATE SUMMARIZECOLUMNS(...)"
                         [query_summary="Revenue by market and product for Q1 2025"]
                         [result_name="revenue by market q1"]  ← names the saved CSV
                         [max_rows=500]                        ← optional row cap for sampling
9. read_query_result     file_path=<savedTo>   [offset=0]   [limit=100]
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

### Large result handling — in detail

| Result size | What happens |
|---|---|
| **≤ 50 rows** | Returned inline as JSON — zero friction |
| **> 50 rows** | Full result saved to a timestamped CSV; agent receives a compact summary |

The summary returned for large results contains:
- `rowCount` — total rows written
- `columns` — column names and types
- `preview` — first 5 rows
- `savedTo` — absolute path to the CSV file

The agent can then page through the file with `read_query_result`:

```
read_query_result(
    file_path = "/path/from/savedTo",
    offset    = 0,      # zero-based row offset
    limit     = 100     # rows per page (default 100)
)
```

Returns `rows`, `totalRows`, `offset`, `limit`, and `hasMore`. Increment `offset` by `limit` to fetch the next page.

**`execute_dax` parameters for controlling result size:**

| Parameter | Type | Description |
|---|---|---|
| `query_summary` | `str` (optional) | Short description of what the user asked for — logged to the local query history for auditability and cross-session reuse. |
| `result_name` | `str` (optional) | Short label used in the CSV filename — e.g. `"gmv by market 2024"` → `dax_result_gmv_by_market_2024_20260305_143022.csv`. Max 40 characters. |
| `max_rows` | `int` (optional) | Hard cap applied via `TOPN` at the Power BI engine level. Useful for quick sampling without rewriting the DAX. |

**Output directory** defaults to `~/powerbi_output`. Override with `POWERBI_OUTPUT_DIR` in your MCP client's `env` block. CSV files and the query history log are not automatically cleaned up — manage the directory manually or add a retention policy.

---

## Limitations

- **Read-only.** Creation, modification, and deletion of Power BI artefacts are not supported.
- `execute_dax` limits: 100,000 rows or 1,000,000 values per query (Power BI API hard cap).
- Rate limit: 120 DAX query requests per minute per user.
- `list_tables`, `list_measures`, and `list_columns` use the DAX `INFO.VIEW.*` functions, which require Import or DirectQuery models with XMLA read access enabled.
- CSV files written by `execute_dax` are not automatically cleaned up.

---

## Security

- Tokens are persisted using OS-native secure storage via [`msal-extensions`](https://github.com/AzureAD/microsoft-authentication-extensions-for-python):
  - **macOS** — Keychain
  - **Windows** — DPAPI-encrypted file
  - **Linux** — LibSecret (gnome-keyring / KWallet); falls back to an encrypted file if unavailable
- The cache file is written to `~/.powerbi_mcp_token_cache.bin` and is covered by `.gitignore`.
- The server never logs access tokens.
- All data access is gated by the user's own Power BI permissions (delegated OAuth 2.0 — no service principal, no client secret).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for project structure, dev environment setup, architecture notes, and how to add new tools.
