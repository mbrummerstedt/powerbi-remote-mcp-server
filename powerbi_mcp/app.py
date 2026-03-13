"""
Power BI MCP Server — application factory and entry point.

Loads settings, creates the FastMCP instance, and registers all tools.
"""

from __future__ import annotations

import sys
import textwrap

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from .config import Settings
from .tools import register_tools

try:
    settings = Settings()
except ValidationError:
    print(
        "Configuration error: POWERBI_CLIENT_ID is not set.\n"
        "Set the environment variable or create a .env file before starting the server.",
        file=sys.stderr,
    )
    sys.exit(1)

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

register_tools(mcp, settings.client_id, settings.tenant_id, settings.output_dir)


def main() -> None:
    mcp.run()
