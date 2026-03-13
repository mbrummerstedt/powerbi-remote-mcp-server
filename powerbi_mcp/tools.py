"""
MCP tools for Power BI operations.

All MCP tool registrations for interacting with Power BI workspaces,
datasets, and executing DAX queries.
"""

from __future__ import annotations

import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from .auth import PowerBIAuth
from .client import PowerBIClient, PowerBIError
from .output import read_csv_page, save_rows_to_csv

INLINE_ROW_LIMIT = 50


def register_tools(
    mcp: FastMCP,
    client_id: str,
    tenant_id: str = "organizations",
    output_dir: str = "powerbi_output",
) -> None:
    """
    Register all Power BI tools with the MCP server.

    Parameters
    ----------
    mcp:
        The FastMCP server instance.
    client_id:
        Azure AD application (client) ID for authentication.
    tenant_id:
        Azure AD tenant ID or "organizations" (default).
    output_dir:
        Directory where large DAX query results are saved as CSV files.
    """
    _auth = PowerBIAuth(client_id, tenant_id)

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

    @mcp.tool()
    async def authenticate() -> str:
        """
        Authenticate with Power BI using the OAuth 2.0 device code flow.

        Call this tool first if you have never logged in, or if a previous call
        returned "Not authenticated".

        The tool uses a two-step flow:
        - First call: returns a URL and a one-time code for you to open in a browser.
        - Second call: completes the authentication after you have signed in.

        Your credentials are cached locally so you will not need to repeat this
        step until the refresh token expires (~90 days).
        """
        import asyncio

        token = _auth.get_token_silent()
        if token:
            return "Already authenticated. No action needed."

        # Phase 2: complete a pending flow started in a previous call
        pending = getattr(_auth, "_pending_flow", None)
        if pending:
            try:
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: _auth.app.acquire_token_by_device_flow(pending),
                    ),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                return (
                    "Still waiting for browser authentication. "
                    "Complete the sign-in then call `authenticate` again."
                )

            if "access_token" in result:
                _auth._pending_flow = None
                return "Authentication successful! You can now use all Power BI tools."

            error = result.get("error", "unknown")
            if error == "authorization_pending":
                return (
                    "Still waiting for browser authentication. "
                    "Complete the sign-in then call `authenticate` again."
                )

            # Flow expired or failed — clear so the next call starts fresh
            _auth._pending_flow = None
            return (
                f"Authentication failed ({error}): "
                f"{result.get('error_description', 'unknown error')}. "
                "Call `authenticate` again to restart."
            )

        # Phase 1: start a new device code flow and return the URL + code
        flow = _auth.initiate_device_flow()
        verification_uri = flow.get("verification_uri", "https://microsoft.com/devicelogin")
        user_code = flow["user_code"]

        return (
            f"## Power BI Authentication\n\n"
            f"1. Open: {verification_uri}\n"
            f"2. Enter code: **{user_code}**\n"
            f"3. Sign in with your Microsoft / Power BI account\n\n"
            f"After completing browser sign-in, call `authenticate` again to finish."
        )

    @mcp.tool()
    async def logout() -> str:
        """
        Sign out of Power BI by clearing the cached credentials.

        After logging out, call `authenticate` to sign in again.
        """
        _auth._pending_flow = None
        _auth.clear_cache()
        return "Logged out. Cached credentials have been cleared. Call `authenticate` to sign in again."

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

        summary = [
            {
                "id": ws.id,
                "name": ws.name,
                "type": ws.type,
                "state": ws.state,
                "isOnDedicatedCapacity": ws.is_on_dedicated_capacity,
            }
            for ws in workspaces
        ]

        return _fmt_json(summary)

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

        summary = [
            {
                "id": ds.id,
                "name": ds.name,
                "configuredBy": ds.configured_by,
                "targetStorageMode": ds.target_storage_mode,
                "isRefreshable": ds.is_refreshable,
                "createdDate": ds.created_date,
                "webUrl": ds.web_url,
            }
            for ds in datasets
        ]

        return _fmt_json(summary)

    @mcp.tool()
    async def get_dataset_info(
        workspace_id: Annotated[
            str, "GUID of the workspace that contains the dataset."
        ],
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
                "id": info.id,
                "name": info.name,
                "description": info.description,
                "configuredBy": info.configured_by,
                "targetStorageMode": info.target_storage_mode,
                "isRefreshable": info.is_refreshable,
                "isEffectiveIdentityRequired": info.is_effective_identity_required,
                "isOnPremGatewayRequired": info.is_on_prem_gateway_required,
                "createdDate": info.created_date,
                "webUrl": info.web_url,
            },
            "recentRefreshes": [
                {
                    "requestId": r.request_id,
                    "status": r.status,
                    "startTime": r.start_time,
                    "endTime": r.end_time,
                    "refreshType": r.refresh_type,
                }
                for r in history
            ],
        }
        return _fmt_json(output)

    @mcp.tool()
    async def list_tables(
        workspace_id: Annotated[
            str, "GUID of the workspace that contains the dataset."
        ],
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

        summary = [
            {
                "name": t.name,
                "description": t.description,
                "isHidden": t.is_hidden,
            }
            for t in tables
        ]

        return _fmt_json(summary)

    @mcp.tool()
    async def list_measures(
        workspace_id: Annotated[
            str, "GUID of the workspace that contains the dataset."
        ],
        dataset_id: Annotated[str, "GUID of the dataset to inspect."],
        table_name: Annotated[
            str | None,
            "Optional: restrict results to measures in this table. "
            "Leave blank to return all measures.",
        ] = None,
    ) -> str:
        """
        List measures defined in a Power BI dataset.

        Returns each measure's name, parent table, description, and format string.
        Optionally filter by table name.
        """
        client = _get_client()
        try:
            measures = await client.list_measures(workspace_id, dataset_id, table_name)
        except PowerBIError as exc:
            return f"Error listing measures: {exc}"

        if not measures:
            filter_note = f" in table {table_name!r}" if table_name else ""
            return f"No visible measures found{filter_note}."

        summary = [
            {
                "name": m.name,
                "tableName": m.table_name,
                "description": m.description,
                "formatString": m.format_string,
            }
            for m in measures
        ]

        return _fmt_json(summary)

    @mcp.tool()
    async def list_columns(
        workspace_id: Annotated[
            str, "GUID of the workspace that contains the dataset."
        ],
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

        summary = [
            {
                "name": c.name,
                "tableName": c.table_name,
                "description": c.description,
                "dataType": c.data_type,
                "isKey": c.is_key,
            }
            for c in columns
        ]

        return _fmt_json(summary)

    @mcp.tool()
    async def execute_dax(
        workspace_id: Annotated[
            str, "GUID of the workspace that contains the dataset."
        ],
        dataset_id: Annotated[str, "GUID of the dataset to query."],
        dax_query: Annotated[
            str,
            "A valid DAX query. Must start with EVALUATE. "
            'Example: "EVALUATE SUMMARIZECOLUMNS(\'Date\'[Year], \\"Sales\\", [Total Sales])"',
        ],
        max_rows: Annotated[
            int | None,
            "Optional hard cap on the number of rows returned. When set, the query "
            "is wrapped in TOPN(<max_rows>, ...) before execution, limiting results "
            "at the Power BI engine level. Useful for sampling large tables.",
        ] = None,
        result_name: Annotated[
            str | None,
            "Optional short label describing what this result contains "
            "(e.g. 'sales by region 2024'). Used in the CSV filename so saved files "
            "are easy to identify later. Maximum 40 characters; special characters "
            "are replaced with underscores. Example filename: "
            "dax_result_sales_by_region_2024_20260305_143022.csv",
        ] = None,
    ) -> str:
        """
        Execute a DAX query against a Power BI dataset and return the result rows.

        The query must start with EVALUATE (standard DAX query syntax).
        Results are returned as a JSON array of objects, with column names as keys.

        Small results (<= 50 rows) are returned inline as JSON.
        Large results (> 50 rows) are automatically saved to a CSV file and a
        compact summary is returned with the file path, column names, row count,
        and a preview of the first 5 rows. Use `read_query_result` to page
        through a saved CSV, or read the file directly.

        Limitations imposed by the Power BI API:
        - Maximum 1,000,000 values or 100,000 rows per query.
        - Rate limit: 120 requests per minute per user.
        - Only DAX is supported; MDX and DMV queries are not.
        - The tenant setting "Dataset Execute Queries REST API" must be enabled.

        Tips:
        - Use TOPN or FILTER to limit large result sets.
        - Use SUMMARIZECOLUMNS for aggregated queries.
        - Use CALCULATETABLE for filtered table expressions.
        - Use max_rows to sample a large table without rewriting the DAX.
        - Use result_name to give the saved CSV a meaningful filename.
        """
        from .client import _parse_dax_rows

        if max_rows is not None and max_rows > 0:
            dax_query = f"EVALUATE TOPN({max_rows}, {dax_query[len('EVALUATE'):].strip()})"

        client = _get_client()
        try:
            raw = await client.execute_dax(workspace_id, dataset_id, dax_query)
        except PowerBIError as exc:
            return f"DAX query error: {exc}"

        rows = _parse_dax_rows(raw)

        if not rows:
            return "Query executed successfully but returned no rows."

        if len(rows) <= INLINE_ROW_LIMIT:
            return _fmt_json({"rowCount": len(rows), "rows": rows})

        try:
            file_path = save_rows_to_csv(rows, output_dir, name=result_name)
        except Exception as exc:
            return _fmt_json({"rowCount": len(rows), "rows": rows, "saveError": str(exc)})

        columns = list(rows[0].keys())
        preview = rows[:5]
        result = {
            "rowCount": len(rows),
            "columns": columns,
            "preview": preview,
            "savedTo": file_path,
            "message": (
                f"Large result ({len(rows)} rows) saved to CSV. "
                "Use `read_query_result` to page through the data, "
                "or read the file directly."
            ),
        }
        return _fmt_json(result)

    @mcp.tool()
    async def read_query_result(
        file_path: Annotated[
            str,
            "Absolute path to a CSV file returned by a previous `execute_dax` call "
            "(the `savedTo` field in the response).",
        ],
        offset: Annotated[
            int,
            "Zero-based row offset to start reading from. Default: 0.",
        ] = 0,
        limit: Annotated[
            int,
            "Maximum number of rows to return. Default: 100.",
        ] = 100,
    ) -> str:
        """
        Read a page of rows from a CSV file saved by `execute_dax`.

        Use this tool when `execute_dax` returns a `savedTo` path instead of
        inline rows. Combine `offset` and `limit` to page through large results
        without loading the entire file into context.

        Returns rows for the requested slice together with pagination metadata:
        - totalRows: total number of rows in the file
        - offset: the offset used
        - limit: the limit used
        - hasMore: whether more rows exist after this page

        Example workflow:
        1. Call `execute_dax` — if rows > 50 you get a savedTo path.
        2. Call `read_query_result(file_path=savedTo, offset=0, limit=100)`.
        3. If hasMore is true, call again with offset=100, then 200, etc.
        """
        try:
            page = read_csv_page(file_path, offset=offset, limit=limit)
        except FileNotFoundError:
            return f"File not found: {file_path!r}. Re-run the DAX query to regenerate it."
        except Exception as exc:
            return f"Error reading result file: {exc}"

        return _fmt_json(page)
