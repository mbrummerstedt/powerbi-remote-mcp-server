"""
Tests for MCP tool functions in powerbi_mcp/tools.py.

Tools are registered on a real FastMCP instance and called via mcp.call_tool().
HTTP is intercepted by respx; PowerBIAuth.get_token_silent is patched to return
a fake token, so no real MSAL flows occur.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import respx
from httpx import Response
from mcp.server.fastmcp import FastMCP

from powerbi_mcp.tools import INLINE_ROW_LIMIT, register_tools
from tests.conftest import (
    DATASET_ID,
    FAKE_TOKEN,
    WORKSPACE_ID,
    make_column_dax_row,
    make_dataset_payload,
    make_dax_response,
    make_measure_dax_row,
    make_refresh_entry_payload,
    make_table_dax_row,
    make_workspace_payload,
)

BASE = "https://api.powerbi.com/v1.0/myorg"


@pytest.fixture
def mcp_with_tools(tmp_path: Path):
    """Create a FastMCP instance with all Power BI tools registered."""
    mcp = FastMCP("Power BI Test")
    with patch(
        "powerbi_mcp.auth.PowerBIAuth.get_token_silent",
        return_value=FAKE_TOKEN,
    ):
        register_tools(mcp, "fake-client-id", output_dir=str(tmp_path))
        yield mcp


async def call(mcp: FastMCP, tool_name: str, **kwargs) -> str:
    """Invoke a tool and return its text response."""
    contents, _ = await mcp.call_tool(tool_name, kwargs)
    return contents[0].text


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


class TestAuthenticate:
    async def test_already_authenticated_short_circuits(self, mcp_with_tools: FastMCP):
        result = await call(mcp_with_tools, "authenticate")
        assert "Already authenticated" in result

    async def test_no_token_initiates_flow(self):
        """Phase 1: no token → returns URL + code, instructs user to call again."""
        mcp = FastMCP("Power BI Test")
        with patch(
            "powerbi_mcp.auth.PowerBIAuth.get_token_silent",
            return_value=None,
        ), patch(
            "powerbi_mcp.auth.PowerBIAuth.initiate_device_flow",
            return_value={
                "message": "Go to https://microsoft.com/devicelogin",
                "user_code": "ABC123",
                "verification_uri": "https://microsoft.com/devicelogin",
            },
        ):
            register_tools(mcp, "fake-client-id")
            result = await call(mcp, "authenticate")
        assert "ABC123" in result
        assert "microsoft.com/devicelogin" in result
        assert "authenticate" in result  # instructs user to call again


# ---------------------------------------------------------------------------
# list_workspaces
# ---------------------------------------------------------------------------


class TestListWorkspaces:
    @respx.mock
    async def test_happy_path_returns_json(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(200, json={"value": [make_workspace_payload()]})
        )
        result = await call(mcp_with_tools, "list_workspaces")
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["id"] == WORKSPACE_ID
        assert data[0]["name"] == "Test Workspace"

    @respx.mock
    async def test_empty_list_returns_human_message(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(200, json={"value": []})
        )
        result = await call(mcp_with_tools, "list_workspaces")
        assert "No workspaces found" in result

    @respx.mock
    async def test_api_error_returns_error_string(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(403, json={"error": {"message": "Forbidden"}})
        )
        result = await call(mcp_with_tools, "list_workspaces")
        assert "Error listing workspaces" in result


# ---------------------------------------------------------------------------
# list_datasets
# ---------------------------------------------------------------------------


class TestListDatasets:
    @respx.mock
    async def test_happy_path_returns_json(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets").mock(
            return_value=Response(200, json={"value": [make_dataset_payload()]})
        )
        result = await call(mcp_with_tools, "list_datasets", workspace_id=WORKSPACE_ID)
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["id"] == DATASET_ID
        assert data[0]["name"] == "Test Dataset"

    @respx.mock
    async def test_empty_returns_human_message(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets").mock(
            return_value=Response(200, json={"value": []})
        )
        result = await call(mcp_with_tools, "list_datasets", workspace_id=WORKSPACE_ID)
        assert "No datasets found" in result

    @respx.mock
    async def test_api_error_returns_error_string(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets").mock(
            return_value=Response(500, json={"error": {"message": "Server Error"}})
        )
        result = await call(mcp_with_tools, "list_datasets", workspace_id=WORKSPACE_ID)
        assert "Error listing datasets" in result


# ---------------------------------------------------------------------------
# get_dataset_info
# ---------------------------------------------------------------------------


class TestGetDatasetInfo:
    @respx.mock
    async def test_returns_dataset_and_refreshes_keys(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}").mock(
            return_value=Response(200, json=make_dataset_payload())
        )
        respx.get(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/refreshes",
        ).mock(
            return_value=Response(200, json={"value": [make_refresh_entry_payload()]})
        )
        result = await call(
            mcp_with_tools, "get_dataset_info",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        data = json.loads(result)
        assert "dataset" in data
        assert "recentRefreshes" in data
        assert data["dataset"]["name"] == "Test Dataset"
        assert len(data["recentRefreshes"]) == 1

    @respx.mock
    async def test_api_error_returns_error_string(self, mcp_with_tools: FastMCP):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}").mock(
            return_value=Response(404, json={"error": {"message": "Not found"}})
        )
        result = await call(
            mcp_with_tools, "get_dataset_info",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        assert "Error retrieving dataset info" in result


# ---------------------------------------------------------------------------
# list_tables
# ---------------------------------------------------------------------------


class TestListTables:
    @respx.mock
    async def test_happy_path_returns_json(self, mcp_with_tools: FastMCP):
        row = make_table_dax_row("Sales")
        bracketed = {f"[{k}]": v for k, v in row.items()}
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([bracketed])))
        result = await call(
            mcp_with_tools, "list_tables",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        data = json.loads(result)
        assert data[0]["name"] == "Sales"

    @respx.mock
    async def test_empty_returns_human_message(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        result = await call(
            mcp_with_tools, "list_tables",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        assert "No visible tables" in result

    @respx.mock
    async def test_api_error_includes_xmla_hint(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(
            return_value=Response(400, json={"error": {"message": "DAX error"}})
        )
        result = await call(
            mcp_with_tools, "list_tables",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        assert "Error listing tables" in result
        assert "XMLA" in result


# ---------------------------------------------------------------------------
# list_measures
# ---------------------------------------------------------------------------


class TestListMeasures:
    @respx.mock
    async def test_returns_all_measures(self, mcp_with_tools: FastMCP):
        row = make_measure_dax_row()
        bracketed = {f"[{k}]": v for k, v in row.items()}
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([bracketed])))
        result = await call(
            mcp_with_tools, "list_measures",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        data = json.loads(result)
        assert data[0]["name"] == "Total Sales"
        assert data[0]["tableName"] == "Sales"

    @respx.mock
    async def test_empty_without_filter_returns_no_filter_note(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        result = await call(
            mcp_with_tools, "list_measures",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        assert "No visible measures found" in result
        assert "in table" not in result

    @respx.mock
    async def test_empty_with_filter_includes_table_note(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        result = await call(
            mcp_with_tools, "list_measures",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            table_name="Sales",
        )
        assert "in table" in result
        assert "Sales" in result


# ---------------------------------------------------------------------------
# list_columns
# ---------------------------------------------------------------------------


class TestListColumns:
    @respx.mock
    async def test_returns_all_columns(self, mcp_with_tools: FastMCP):
        row = make_column_dax_row()
        bracketed = {f"[{k}]": v for k, v in row.items()}
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([bracketed])))
        result = await call(
            mcp_with_tools, "list_columns",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        data = json.loads(result)
        assert data[0]["name"] == "ProductName"
        assert data[0]["tableName"] == "Products"

    @respx.mock
    async def test_empty_with_table_filter_includes_note(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        result = await call(
            mcp_with_tools, "list_columns",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            table_name="Products",
        )
        assert "in table" in result
        assert "Products" in result

    @respx.mock
    async def test_api_error_returns_error_string(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(500, json={"error": {"message": "Server error"}}))
        result = await call(
            mcp_with_tools, "list_columns",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
        )
        assert "Error listing columns" in result


# ---------------------------------------------------------------------------
# execute_dax
# ---------------------------------------------------------------------------


class TestExecuteDax:
    @respx.mock
    async def test_returns_row_count_and_rows(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(
            return_value=Response(
                200,
                json=make_dax_response([{"[x]": 1}, {"[x]": 2}]),
            )
        )
        result = await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query='EVALUATE ROW("x", 1)',
        )
        data = json.loads(result)
        assert data["rowCount"] == 2
        assert len(data["rows"]) == 2

    @respx.mock
    async def test_zero_rows_returns_specific_message(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        result = await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query="EVALUATE FILTER(Sales, FALSE())",
        )
        assert "returned no rows" in result

    @respx.mock
    async def test_api_error_returns_error_string(self, mcp_with_tools: FastMCP):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(
            return_value=Response(400, json={"error": {"message": "Invalid DAX syntax"}})
        )
        result = await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query="EVALUATE INVALID",
        )
        assert "DAX query error" in result

    @respx.mock
    async def test_large_result_saves_csv_and_returns_summary(
        self, mcp_with_tools: FastMCP
    ):
        """Results > INLINE_ROW_LIMIT should be saved to CSV; tool returns summary."""
        large_rows = [{"[x]": i} for i in range(INLINE_ROW_LIMIT + 1)]
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response(large_rows)))

        result = await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query="EVALUATE Sales",
        )
        data = json.loads(result)
        assert data["rowCount"] == INLINE_ROW_LIMIT + 1
        assert "savedTo" in data
        assert "columns" in data
        assert "preview" in data
        assert len(data["preview"]) <= 5
        assert Path(data["savedTo"]).exists()

    @respx.mock
    async def test_result_name_used_in_csv_filename(self, mcp_with_tools: FastMCP):
        """result_name parameter should appear in the saved CSV filename."""
        large_rows = [{"[x]": i} for i in range(INLINE_ROW_LIMIT + 1)]
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response(large_rows)))

        result = await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query="EVALUATE Sales",
            result_name="monthly revenue 2024",
        )
        data = json.loads(result)
        assert "savedTo" in data
        assert "monthly_revenue_2024" in Path(data["savedTo"]).name

    @respx.mock
    async def test_small_result_returned_inline(self, mcp_with_tools: FastMCP):
        """Results <= INLINE_ROW_LIMIT should be returned inline without savedTo."""
        small_rows = [{"[x]": i} for i in range(INLINE_ROW_LIMIT)]
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response(small_rows)))

        result = await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query="EVALUATE TOPN(50, Sales)",
        )
        data = json.loads(result)
        assert data["rowCount"] == INLINE_ROW_LIMIT
        assert "rows" in data
        assert "savedTo" not in data

    @respx.mock
    async def test_max_rows_wraps_query_in_topn(self, mcp_with_tools: FastMCP):
        """max_rows parameter should result in a TOPN-wrapped query being sent."""
        captured: list[str] = []

        def capture(request, route):
            body = json.loads(request.content)
            captured.append(body["queries"][0]["query"])
            return Response(200, json=make_dax_response([{"[x]": 1}]))

        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(side_effect=capture)

        await call(
            mcp_with_tools, "execute_dax",
            workspace_id=WORKSPACE_ID,
            dataset_id=DATASET_ID,
            dax_query="EVALUATE Sales",
            max_rows=10,
        )
        assert captured, "No query was captured"
        assert "TOPN(10" in captured[0]


# ---------------------------------------------------------------------------
# read_query_result
# ---------------------------------------------------------------------------


def _make_csv(path: Path, n_rows: int) -> Path:
    """Write a simple CSV with n_rows data rows."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "value"])
        writer.writeheader()
        for i in range(n_rows):
            writer.writerow({"id": i, "value": f"v{i}"})
    return path


class TestReadQueryResult:
    async def test_first_page(self, mcp_with_tools: FastMCP, tmp_path: Path):
        csv_file = _make_csv(tmp_path / "result.csv", 200)
        result = await call(
            mcp_with_tools, "read_query_result",
            file_path=str(csv_file),
            offset=0,
            limit=50,
        )
        data = json.loads(result)
        assert len(data["rows"]) == 50
        assert data["totalRows"] == 200
        assert data["hasMore"] is True
        assert data["offset"] == 0

    async def test_last_page_has_more_false(self, mcp_with_tools: FastMCP, tmp_path: Path):
        csv_file = _make_csv(tmp_path / "result.csv", 10)
        result = await call(
            mcp_with_tools, "read_query_result",
            file_path=str(csv_file),
            offset=8,
            limit=10,
        )
        data = json.loads(result)
        assert len(data["rows"]) == 2
        assert data["hasMore"] is False

    async def test_file_not_found_returns_friendly_message(
        self, mcp_with_tools: FastMCP, tmp_path: Path
    ):
        result = await call(
            mcp_with_tools, "read_query_result",
            file_path=str(tmp_path / "nonexistent.csv"),
        )
        assert "File not found" in result

    async def test_default_limit_is_100(self, mcp_with_tools: FastMCP, tmp_path: Path):
        csv_file = _make_csv(tmp_path / "result.csv", 300)
        result = await call(
            mcp_with_tools, "read_query_result",
            file_path=str(csv_file),
        )
        data = json.loads(result)
        assert len(data["rows"]) == 100
