"""
Async Power BI REST API client.

All dataset operations are group-scoped, i.e. they use
  GET /v1.0/myorg/groups/{workspace_id}/datasets/...
because datasets live inside workspaces (groups) in Power BI.

Reference: https://learn.microsoft.com/en-us/rest/api/power-bi/
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .models import Column, Dataset, Measure, RefreshEntry, Table, Workspace

BASE_URL = "https://api.powerbi.com/v1.0/myorg"
DEFAULT_TIMEOUT = 120


class PowerBIError(Exception):
    """Raised when the Power BI API returns an error response."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Power BI API error {status_code}: {message}")


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            detail = response.text
        raise PowerBIError(response.status_code, detail)


class PowerBIClient:
    """
    Thin async wrapper around the Power BI REST API.

    Parameters
    ----------
    token:
        A valid Bearer access token obtained from MSAL.
    """

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def list_workspaces(self) -> list[Workspace]:
        """Return all workspaces the authenticated user is a member of."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(f"{BASE_URL}/groups", headers=self._headers)
            _raise_for_status(resp)
            data = resp.json().get("value", [])
            return [Workspace.model_validate(ws) for ws in data]

    async def list_datasets(self, workspace_id: str) -> list[Dataset]:
        """
        Return all datasets in a specific workspace.

        Parameters
        ----------
        workspace_id:
            The GUID of the workspace (group).
        """
        url = f"{BASE_URL}/groups/{workspace_id}/datasets"
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers=self._headers)
            _raise_for_status(resp)
            data = resp.json().get("value", [])
            return [Dataset.model_validate(ds) for ds in data]

    async def get_dataset(self, workspace_id: str, dataset_id: str) -> Dataset:
        """Return metadata for a single dataset."""
        url = f"{BASE_URL}/groups/{workspace_id}/datasets/{dataset_id}"
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers=self._headers)
            _raise_for_status(resp)
            return Dataset.model_validate(resp.json())

    async def get_dataset_refresh_history(
        self, workspace_id: str, dataset_id: str, top: int = 10
    ) -> list[RefreshEntry]:
        """Return recent refresh history for a dataset."""
        url = (
            f"{BASE_URL}/groups/{workspace_id}/datasets/{dataset_id}"
            f"/refreshes?$top={top}"
        )
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers=self._headers)
            _raise_for_status(resp)
            data = resp.json().get("value", [])
            return [RefreshEntry.model_validate(entry) for entry in data]

    async def execute_dax(
        self,
        workspace_id: str,
        dataset_id: str,
        dax_query: str,
        include_nulls: bool = True,
    ) -> dict[str, Any]:
        """
        Execute a DAX query against a dataset and return the raw API response.

        The response shape is:
        {
          "results": [
            {
              "tables": [
                {
                  "rows": [{"[Column]": value, ...}, ...]
                }
              ]
            }
          ]
        }

        Parameters
        ----------
        workspace_id:
            GUID of the workspace that owns the dataset.
        dataset_id:
            GUID of the dataset to query.
        dax_query:
            A valid DAX query string (must start with EVALUATE).
        include_nulls:
            Whether to include null values in the result rows.
        """
        url = (
            f"{BASE_URL}/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
        )
        payload = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": include_nulls},
        }
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            _raise_for_status(resp)
            return resp.json()

    async def list_tables(self, workspace_id: str, dataset_id: str) -> list[Table]:
        """
        Return user-visible tables in a dataset.

        Filters out hidden tables and internal Power BI system tables
        (those whose names start with '$').
        """
        dax = """
EVALUATE
SELECTCOLUMNS(
    FILTER(
        INFO.VIEW.TABLES(),
        NOT [IsHidden] && LEFT([Name], 1) <> "$"
    ),
    "Name",        [Name],
    "Description", [Description],
    "IsHidden",    [IsHidden]
)
ORDER BY [Name]
""".strip()
        result = await self.execute_dax(workspace_id, dataset_id, dax)
        rows = _parse_dax_rows(result)
        return [Table.model_validate(row) for row in rows]

    async def list_measures(
        self, workspace_id: str, dataset_id: str, table_name: str | None = None
    ) -> list[Measure]:
        """
        Return measures defined in the dataset.

        INFO.VIEW.MEASURES() exposes the table name via the [Table] column
        (not [TableName] as the older INFO.MEASURES() schema used).

        Parameters
        ----------
        table_name:
            If provided, only return measures from that table.
        """
        table_filter = (
            f'\n    && [Table] = "{table_name}"'
            if table_name
            else ""
        )
        dax = f"""
EVALUATE
SELECTCOLUMNS(
    FILTER(
        INFO.VIEW.MEASURES(),
        NOT [IsHidden]{table_filter}
    ),
    "Name",         [Name],
    "TableName",    [Table],
    "Description",  [Description],
    "FormatString", [FormatString],
    "Expression",   [Expression]
)
ORDER BY [TableName], [Name]
""".strip()
        result = await self.execute_dax(workspace_id, dataset_id, dax)
        rows = _parse_dax_rows(result)
        return [Measure.model_validate(row) for row in rows]

    async def list_columns(
        self, workspace_id: str, dataset_id: str, table_name: str | None = None
    ) -> list[Column]:
        """
        Return columns (dimensions) defined in the dataset.

        INFO.VIEW.COLUMNS() exposes the table name via [Table] (not [TableName]),
        uses [Name] and [DataType] directly (not [ExplicitName]/[ExplicitDataType]),
        and [Type] is a string: "Data" selects regular source columns only
        (excludes "Calculated", "CalculatedTableColumn", "RowNumber", etc.).

        Parameters
        ----------
        table_name:
            If provided, only return columns from that table.
        """
        table_filter = (
            f'\n    && [Table] = "{table_name}"'
            if table_name
            else ""
        )
        dax = f"""
EVALUATE
SELECTCOLUMNS(
    FILTER(
        INFO.VIEW.COLUMNS(),
        NOT [IsHidden] && [Type] = "Data"{table_filter}
    ),
    "Name",         [Name],
    "TableName",    [Table],
    "Description",  [Description],
    "DataType",     [DataType],
    "IsKey",        [IsKey]
)
ORDER BY [TableName], [Name]
""".strip()
        result = await self.execute_dax(workspace_id, dataset_id, dax)
        rows = _parse_dax_rows(result)
        return [Column.model_validate(row) for row in rows]


def _parse_dax_rows(api_response: dict[str, Any]) -> list[dict]:
    """
    Flatten the nested DAX executeQueries response into a plain list of dicts.

    Power BI returns column names prefixed with the table name in brackets,
    e.g. ``"[Name]"``.  This helper strips those brackets for cleaner output.
    """
    rows: list[dict] = []
    for result in api_response.get("results", []):
        for table in result.get("tables", []):
            for row in table.get("rows", []):
                clean_row = {_strip_brackets(k): v for k, v in row.items()}
                rows.append(clean_row)
    return rows


def _strip_brackets(name: str) -> str:
    """Remove leading [ and trailing ] from a DAX column name."""
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1]
    return name
