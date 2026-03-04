"""
Power BI MCP Server
===================

An MCP server that exposes Power BI semantic models as analysis tools.

Authentication
--------------
Uses OAuth 2.0 device code flow (Microsoft Identity Platform).
Run  ``python server.py --login``  once to cache credentials, then start the
server normally.  Tokens are refreshed automatically by MSAL.

Required environment variable
------------------------------
POWERBI_CLIENT_ID  – Azure AD application (client) ID registered for this app.

Tools exposed
-------------
authenticate          – Initiate / refresh OAuth login (device code flow).
list_workspaces       – List Power BI workspaces the user is a member of.
list_datasets         – List datasets in a workspace.
get_dataset_info      – Detailed metadata for a single dataset.
list_tables           – List visible tables in a dataset.
list_measures         – List measures (optionally filtered by table).
list_columns          – List columns / dimensions (optionally filtered by table).
execute_dax           – Execute a DAX query and return the result rows.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from typing import Annotated

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from auth import PowerBIAuth
from powerbi_client import PowerBIClient, PowerBIError

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

load_dotenv()

CLIENT_ID = os.getenv("POWERBI_CLIENT_ID", "")
if not CLIENT_ID:
    raise EnvironmentError(
        "POWERBI_CLIENT_ID is not set. "
        "Create a .env file or export the variable before starting the server."
    )

mcp = FastMCP(
    "Power BI",
    instructions=textwrap.dedent(
        """
        This server gives you read-only access to Power BI semantic models
        (datasets) via the Power BI REST API.

        Typical workflow:
        1. Call `authenticate` if this is the first run or the token has expired.
        2. Call `list_workspaces` to find the workspace_id that contains the
           dataset you want to analyse.
        3. Call `list_datasets` with that workspace_id.
        4. Call `list_tables`, `list_measures`, or `list_columns` to explore
           the data model structure.
        5. Call `execute_dax` to retrieve data using a DAX query.

        All dataset operations require BOTH a workspace_id AND a dataset_id
        because datasets always belong to a workspace (group) in Power BI.
        """
    ),
)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_auth = PowerBIAuth(CLIENT_ID)


def _get_client() -> PowerBIClient:
    """Return an authenticated API client, raising if no token is available."""
    token = _auth.get_token_silent()
    if token is None:
        raise RuntimeError(
            "Not authenticated. Call the `authenticate` tool first, then retry."
        )
    return PowerBIClient(token)


def _fmt_json(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Tool: authenticate
# ---------------------------------------------------------------------------


@mcp.tool()
async def authenticate() -> str:
    """
    Authenticate with Power BI using the OAuth 2.0 device code flow.

    Call this tool first if you have never logged in, or if a previous call
    returned "Not authenticated".

    The tool will return a short URL and a one-time code.  Open the URL in a
    browser (on any device), enter the code, and sign in with your
    Microsoft / Power BI account.  The tool waits for you to finish and then
    confirms success.  Your credentials are cached locally so you will not
    need to repeat this step until the refresh token expires (~90 days).
    """
    # If a valid cached token already exists, skip the device flow.
    token = _auth.get_token_silent()
    if token:
        return "Already authenticated. No action needed."

    flow = _auth.initiate_device_flow()

    # The MSAL message contains everything the user needs.
    instructions = flow["message"]

    # Now block until the user completes the browser step (up to ~15 min).
    try:
        _auth.complete_device_flow(flow)
    except RuntimeError as exc:
        return f"Authentication failed: {exc}"

    return (
        f"{instructions}\n\n"
        "Authentication successful! You can now use all Power BI tools."
    )


# ---------------------------------------------------------------------------
# Tool: list_workspaces
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_workspaces() -> str:
    """
    List all Power BI workspaces (groups) the authenticated user is a member of.

    Returns workspace id, name, type, and capacity information.
    Use the `id` field as `workspace_id` in subsequent tools.
    """
    client = _get_client()
    try:
        workspaces = await client.list_workspaces()
    except PowerBIError as exc:
        return f"Error listing workspaces: {exc}"

    if not workspaces:
        return "No workspaces found. The user may not be a member of any workspace."

    # Return a concise summary table plus the raw JSON for completeness.
    summary = []
    for ws in workspaces:
        summary.append(
            {
                "id": ws.get("id"),
                "name": ws.get("name"),
                "type": ws.get("type"),
                "state": ws.get("state"),
                "isOnDedicatedCapacity": ws.get("isOnDedicatedCapacity"),
            }
        )

    return _fmt_json(summary)


# ---------------------------------------------------------------------------
# Tool: list_datasets
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_datasets(
    workspace_id: Annotated[
        str,
        "The GUID of the Power BI workspace (group) to list datasets from. "
        "Obtain this from `list_workspaces`.",
    ],
) -> str:
    """
    List all datasets (semantic models) in a Power BI workspace.

    Returns dataset id, name, configured-by, web URL, is-refreshable flag,
    and the target storage mode (Import / DirectQuery / etc.).
    Use the `id` field as `dataset_id` in subsequent tools.
    """
    client = _get_client()
    try:
        datasets = await client.list_datasets(workspace_id)
    except PowerBIError as exc:
        return f"Error listing datasets: {exc}"

    if not datasets:
        return f"No datasets found in workspace {workspace_id!r}."

    summary = []
    for ds in datasets:
        summary.append(
            {
                "id": ds.get("id"),
                "name": ds.get("name"),
                "configuredBy": ds.get("configuredBy"),
                "targetStorageMode": ds.get("targetStorageMode"),
                "isRefreshable": ds.get("isRefreshable"),
                "createdDate": ds.get("createdDate"),
                "webUrl": ds.get("webUrl"),
            }
        )

    return _fmt_json(summary)


# ---------------------------------------------------------------------------
# Tool: get_dataset_info
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_dataset_info(
    workspace_id: Annotated[str, "GUID of the workspace that contains the dataset."],
    dataset_id: Annotated[str, "GUID of the dataset to inspect."],
) -> str:
    """
    Return detailed metadata for a single Power BI dataset.

    Includes name, owner, refresh schedule, storage mode, web URL, and more.
    Also returns the last 5 refresh history entries so you can see data freshness.
    """
    client = _get_client()
    try:
        info = await client.get_dataset(workspace_id, dataset_id)
        history = await client.get_dataset_refresh_history(
            workspace_id, dataset_id, top=5
        )
    except PowerBIError as exc:
        return f"Error retrieving dataset info: {exc}"

    output = {
        "dataset": {
            "id": info.get("id"),
            "name": info.get("name"),
            "description": info.get("description"),
            "configuredBy": info.get("configuredBy"),
            "targetStorageMode": info.get("targetStorageMode"),
            "isRefreshable": info.get("isRefreshable"),
            "isEffectiveIdentityRequired": info.get("isEffectiveIdentityRequired"),
            "isOnPremGatewayRequired": info.get("isOnPremGatewayRequired"),
            "createdDate": info.get("createdDate"),
            "webUrl": info.get("webUrl"),
        },
        "recentRefreshes": [
            {
                "requestId": r.get("requestId"),
                "status": r.get("status"),
                "startTime": r.get("startTime"),
                "endTime": r.get("endTime"),
                "refreshType": r.get("refreshType"),
            }
            for r in history
        ],
    }
    return _fmt_json(output)


# ---------------------------------------------------------------------------
# Tool: list_tables
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_tables(
    workspace_id: Annotated[str, "GUID of the workspace that contains the dataset."],
    dataset_id: Annotated[str, "GUID of the dataset to inspect."],
) -> str:
    """
    List all visible tables in a Power BI dataset.

    Hidden tables and internal Power BI system tables (names starting with '$')
    are excluded.  Use the returned table names in `list_measures`,
    `list_columns`, and DAX queries.
    """
    client = _get_client()
    try:
        tables = await client.list_tables(workspace_id, dataset_id)
    except PowerBIError as exc:
        return (
            f"Error listing tables: {exc}\n\n"
            "Note: listing tables requires the dataset to support DAX "
            "INFO.VIEW functions (available on Import / DirectQuery models "
            "with XMLA read access enabled)."
        )

    if not tables:
        return "No visible tables found in this dataset."

    return _fmt_json(tables)


# ---------------------------------------------------------------------------
# Tool: list_measures
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_measures(
    workspace_id: Annotated[str, "GUID of the workspace that contains the dataset."],
    dataset_id: Annotated[str, "GUID of the dataset to inspect."],
    table_name: Annotated[
        str | None,
        "Optional: restrict results to measures in this table. "
        "Leave blank to return all measures.",
    ] = None,
) -> str:
    """
    List measures defined in a Power BI dataset.

    Returns each measure's name, parent table, description, format string,
    and DAX expression.  Optionally filter by table name.
    """
    client = _get_client()
    try:
        measures = await client.list_measures(workspace_id, dataset_id, table_name)
    except PowerBIError as exc:
        return f"Error listing measures: {exc}"

    if not measures:
        filter_note = f" in table {table_name!r}" if table_name else ""
        return f"No visible measures found{filter_note}."

    return _fmt_json(measures)


# ---------------------------------------------------------------------------
# Tool: list_columns
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_columns(
    workspace_id: Annotated[str, "GUID of the workspace that contains the dataset."],
    dataset_id: Annotated[str, "GUID of the dataset to inspect."],
    table_name: Annotated[
        str | None,
        "Optional: restrict results to columns in this table. "
        "Leave blank to return columns from all tables.",
    ] = None,
) -> str:
    """
    List columns (dimensions) in a Power BI dataset.

    Returns each column's name, parent table, description, data type, and
    whether it is a key column.  Optionally filter by table name.
    """
    client = _get_client()
    try:
        columns = await client.list_columns(workspace_id, dataset_id, table_name)
    except PowerBIError as exc:
        return f"Error listing columns: {exc}"

    if not columns:
        filter_note = f" in table {table_name!r}" if table_name else ""
        return f"No visible columns found{filter_note}."

    return _fmt_json(columns)


# ---------------------------------------------------------------------------
# Tool: execute_dax
# ---------------------------------------------------------------------------


@mcp.tool()
async def execute_dax(
    workspace_id: Annotated[str, "GUID of the workspace that contains the dataset."],
    dataset_id: Annotated[str, "GUID of the dataset to query."],
    dax_query: Annotated[
        str,
        "A valid DAX query. Must start with EVALUATE. "
        "Example: \"EVALUATE SUMMARIZECOLUMNS('Date'[Year], \\\"Sales\\\", [Total Sales])\"",
    ],
) -> str:
    """
    Execute a DAX query against a Power BI dataset and return the result rows.

    The query must start with EVALUATE (standard DAX query syntax).
    Results are returned as a JSON array of objects, with column names as keys.

    Limitations imposed by the Power BI API:
    - Maximum 1,000,000 values or 100,000 rows per query.
    - Rate limit: 120 requests per minute per user.
    - Only DAX is supported; MDX and DMV queries are not.
    - The tenant setting "Dataset Execute Queries REST API" must be enabled.

    Tips:
    - Use TOPN or FILTER to limit large result sets.
    - Use SUMMARIZECOLUMNS for aggregated queries.
    - Use CALCULATETABLE for filtered table expressions.
    """
    client = _get_client()
    try:
        raw = await client.execute_dax(workspace_id, dataset_id, dax_query)
    except PowerBIError as exc:
        return f"DAX query error: {exc}"

    from powerbi_client import _parse_dax_rows

    rows = _parse_dax_rows(raw)

    if not rows:
        return "Query executed successfully but returned no rows."

    result = {
        "rowCount": len(rows),
        "rows": rows,
    }
    return _fmt_json(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--login" in sys.argv:
        # Convenience: run interactive login outside of the MCP server loop.
        print("Starting Power BI login (device code flow)…")
        auth = PowerBIAuth(CLIENT_ID)
        existing = auth.get_token_silent()
        if existing:
            print("Already authenticated. Token is valid.")
            sys.exit(0)
        flow = auth.initiate_device_flow()
        print(flow["message"])
        try:
            auth.complete_device_flow(flow)
            print("Login successful. Token cached.")
        except RuntimeError as e:
            print(f"Login failed: {e}")
            sys.exit(1)
    else:
        mcp.run()
