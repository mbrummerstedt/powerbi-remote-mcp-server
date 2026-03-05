"""
Tests for PowerBIClient and helper functions in powerbi_mcp/client.py.

HTTP calls are intercepted by respx so no real network traffic occurs.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from powerbi_mcp.client import (
    PowerBIClient,
    PowerBIError,
    _parse_dax_rows,
    _strip_brackets,
)
from powerbi_mcp.models import Column, Dataset, Measure, RefreshEntry, Table, Workspace
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
def client() -> PowerBIClient:
    return PowerBIClient(FAKE_TOKEN)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestStripBrackets:
    def test_strips_bracketed_name(self):
        assert _strip_brackets("[Name]") == "Name"

    def test_strips_multi_word_name(self):
        assert _strip_brackets("[Total Sales]") == "Total Sales"

    def test_leaves_plain_string_unchanged(self):
        assert _strip_brackets("Name") == "Name"

    def test_leaves_partial_bracket_unchanged(self):
        assert _strip_brackets("[Name") == "[Name"
        assert _strip_brackets("Name]") == "Name]"

    def test_empty_brackets_become_empty_string(self):
        assert _strip_brackets("[]") == ""


class TestParseDaxRows:
    def test_flattens_nested_structure(self):
        raw = make_dax_response([{"[Name]": "Sales", "[IsHidden]": False}])
        rows = _parse_dax_rows(raw)
        assert rows == [{"Name": "Sales", "IsHidden": False}]

    def test_strips_brackets_from_keys(self):
        raw = make_dax_response([{"[TableName]": "T", "[Expression]": "SUM(x)"}])
        rows = _parse_dax_rows(raw)
        assert rows[0] == {"TableName": "T", "Expression": "SUM(x)"}

    def test_multiple_rows(self):
        raw = make_dax_response([
            {"[Name]": "A"},
            {"[Name]": "B"},
            {"[Name]": "C"},
        ])
        rows = _parse_dax_rows(raw)
        assert len(rows) == 3
        assert [r["Name"] for r in rows] == ["A", "B", "C"]

    def test_empty_results(self):
        assert _parse_dax_rows({}) == []
        assert _parse_dax_rows({"results": []}) == []

    def test_empty_rows(self):
        raw = {"results": [{"tables": [{"rows": []}]}]}
        assert _parse_dax_rows(raw) == []


# ---------------------------------------------------------------------------
# PowerBIClient tests
# ---------------------------------------------------------------------------


class TestListWorkspaces:
    @respx.mock
    async def test_returns_typed_models(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(200, json={"value": [make_workspace_payload()]})
        )
        result = await client.list_workspaces()
        assert len(result) == 1
        assert isinstance(result[0], Workspace)
        assert result[0].id == WORKSPACE_ID

    @respx.mock
    async def test_correct_url(self, client: PowerBIClient):
        route = respx.get(f"{BASE}/groups").mock(
            return_value=Response(200, json={"value": []})
        )
        await client.list_workspaces()
        assert route.called

    @respx.mock
    async def test_empty_value_returns_empty_list(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(200, json={"value": []})
        )
        assert await client.list_workspaces() == []

    @respx.mock
    async def test_api_error_raises_powerbi_error(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(401, json={"error": {"message": "Unauthorized"}})
        )
        with pytest.raises(PowerBIError) as exc_info:
            await client.list_workspaces()
        assert exc_info.value.status_code == 401


class TestListDatasets:
    @respx.mock
    async def test_returns_typed_models(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets").mock(
            return_value=Response(200, json={"value": [make_dataset_payload()]})
        )
        result = await client.list_datasets(WORKSPACE_ID)
        assert len(result) == 1
        assert isinstance(result[0], Dataset)
        assert result[0].id == DATASET_ID

    @respx.mock
    async def test_uses_workspace_scoped_url(self, client: PowerBIClient):
        route = respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets").mock(
            return_value=Response(200, json={"value": []})
        )
        await client.list_datasets(WORKSPACE_ID)
        assert route.called


class TestGetDataset:
    @respx.mock
    async def test_returns_single_dataset(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}").mock(
            return_value=Response(200, json=make_dataset_payload())
        )
        result = await client.get_dataset(WORKSPACE_ID, DATASET_ID)
        assert isinstance(result, Dataset)
        assert result.name == "Test Dataset"

    @respx.mock
    async def test_api_error_raises(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}").mock(
            return_value=Response(404, json={"error": {"message": "Not found"}})
        )
        with pytest.raises(PowerBIError) as exc_info:
            await client.get_dataset(WORKSPACE_ID, DATASET_ID)
        assert exc_info.value.status_code == 404


class TestGetDatasetRefreshHistory:
    @respx.mock
    async def test_returns_typed_entries(self, client: PowerBIClient):
        respx.get(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/refreshes",
            params={"$top": "5"},
        ).mock(
            return_value=Response(200, json={"value": [make_refresh_entry_payload()]})
        )
        result = await client.get_dataset_refresh_history(WORKSPACE_ID, DATASET_ID, top=5)
        assert len(result) == 1
        assert isinstance(result[0], RefreshEntry)
        assert result[0].status == "Completed"

    @respx.mock
    async def test_includes_top_query_param(self, client: PowerBIClient):
        route = respx.get(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/refreshes",
        ).mock(return_value=Response(200, json={"value": []}))
        await client.get_dataset_refresh_history(WORKSPACE_ID, DATASET_ID, top=3)
        assert route.called
        assert "3" in str(route.calls.last.request.url)


class TestExecuteDax:
    @respx.mock
    async def test_posts_to_correct_url(self, client: PowerBIClient):
        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        await client.execute_dax(WORKSPACE_ID, DATASET_ID, "EVALUATE ROW(\"x\", 1)")
        assert route.called

    @respx.mock
    async def test_payload_contains_queries_key(self, client: PowerBIClient):
        import json

        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        dax = "EVALUATE ROW(\"x\", 1)"
        await client.execute_dax(WORKSPACE_ID, DATASET_ID, dax)
        body = json.loads(route.calls.last.request.content)
        assert "queries" in body
        assert body["queries"][0]["query"] == dax

    @respx.mock
    async def test_api_error_raises(self, client: PowerBIClient):
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(400, json={"error": {"message": "Invalid DAX"}}))
        with pytest.raises(PowerBIError) as exc_info:
            await client.execute_dax(WORKSPACE_ID, DATASET_ID, "INVALID")
        assert exc_info.value.status_code == 400


class TestListTables:
    @respx.mock
    async def test_returns_typed_table_models(self, client: PowerBIClient):
        row = make_table_dax_row("Sales")
        bracketed = {f"[{k}]": v for k, v in row.items()}
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([bracketed])))
        result = await client.list_tables(WORKSPACE_ID, DATASET_ID)
        assert len(result) == 1
        assert isinstance(result[0], Table)
        assert result[0].name == "Sales"

    @respx.mock
    async def test_dax_query_references_info_view_tables(self, client: PowerBIClient):
        import json

        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        await client.list_tables(WORKSPACE_ID, DATASET_ID)
        body = json.loads(route.calls.last.request.content)
        assert "INFO.VIEW.TABLES()" in body["queries"][0]["query"]


class TestListMeasures:
    @respx.mock
    async def test_returns_all_measures(self, client: PowerBIClient):
        row = make_measure_dax_row()
        bracketed = {f"[{k}]": v for k, v in row.items()}
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([bracketed])))
        result = await client.list_measures(WORKSPACE_ID, DATASET_ID)
        assert len(result) == 1
        assert isinstance(result[0], Measure)
        assert result[0].name == "Total Sales"

    @respx.mock
    async def test_dax_query_uses_info_view_measures(self, client: PowerBIClient):
        import json

        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        await client.list_measures(WORKSPACE_ID, DATASET_ID)
        body = json.loads(route.calls.last.request.content)
        query = body["queries"][0]["query"]
        assert "INFO.VIEW.MEASURES()" in query
        assert "[Table]" in query

    @respx.mock
    async def test_filter_by_table_name_in_dax(self, client: PowerBIClient):
        import json

        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        await client.list_measures(WORKSPACE_ID, DATASET_ID, table_name="Sales")
        body = json.loads(route.calls.last.request.content)
        assert '"Sales"' in body["queries"][0]["query"]


class TestListColumns:
    @respx.mock
    async def test_returns_typed_column_models(self, client: PowerBIClient):
        row = make_column_dax_row()
        bracketed = {f"[{k}]": v for k, v in row.items()}
        respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([bracketed])))
        result = await client.list_columns(WORKSPACE_ID, DATASET_ID)
        assert len(result) == 1
        assert isinstance(result[0], Column)
        assert result[0].name == "ProductName"

    @respx.mock
    async def test_dax_query_uses_info_view_columns(self, client: PowerBIClient):
        import json

        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        await client.list_columns(WORKSPACE_ID, DATASET_ID)
        body = json.loads(route.calls.last.request.content)
        query = body["queries"][0]["query"]
        assert "INFO.VIEW.COLUMNS()" in query
        assert '[Type] = "Data"' in query
        assert "[Table]" in query

    @respx.mock
    async def test_filter_by_table_name_in_dax(self, client: PowerBIClient):
        import json

        route = respx.post(
            f"{BASE}/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/executeQueries"
        ).mock(return_value=Response(200, json=make_dax_response([])))
        await client.list_columns(WORKSPACE_ID, DATASET_ID, table_name="Products")
        body = json.loads(route.calls.last.request.content)
        assert '"Products"' in body["queries"][0]["query"]


class TestRaiseForStatus:
    @respx.mock
    async def test_401_raises_powerbi_error(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(401, json={"error": {"message": "Unauthorized"}})
        )
        with pytest.raises(PowerBIError) as exc_info:
            await client.list_workspaces()
        assert exc_info.value.status_code == 401
        assert "401" in str(exc_info.value)

    @respx.mock
    async def test_500_raises_powerbi_error(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(500, text="Internal Server Error")
        )
        with pytest.raises(PowerBIError) as exc_info:
            await client.list_workspaces()
        assert exc_info.value.status_code == 500

    @respx.mock
    async def test_200_does_not_raise(self, client: PowerBIClient):
        respx.get(f"{BASE}/groups").mock(
            return_value=Response(200, json={"value": []})
        )
        result = await client.list_workspaces()
        assert result == []
