"""
Power BI MCP Server — CLI entry point.

Normal usage (MCP client connects via stdio):
    python server.py

Terminal login helper (optional — authentication also works via the
`authenticate` tool inside the MCP session):
    python server.py --login
"""

from __future__ import annotations

import sys

from powerbi_mcp.app import main, mcp, settings
from powerbi_mcp.auth import PowerBIAuth

if __name__ == "__main__":
    if "--login" in sys.argv:
        print("Starting Power BI login (device code flow)…")
        auth = PowerBIAuth(settings.client_id, settings.tenant_id)
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
        main()
