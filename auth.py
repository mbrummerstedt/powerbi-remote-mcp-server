"""
OAuth authentication for Power BI using MSAL device code flow.

The user authenticates interactively via their browser; their delegated
permissions determine what data is accessible through the Power BI REST API.
Tokens are cached locally so subsequent calls skip the browser step.
"""

import os
from pathlib import Path

import msal

AUTHORITY = "https://login.microsoftonline.com/common"

# Delegated scopes required for the tools in this server:
#   Dataset.Read.All  – list datasets, execute queries, read metadata
#   Workspace.Read.All – list workspaces (groups)
SCOPES = [
    "https://analysis.windows.net/powerbi/api/Dataset.Read.All",
    "https://analysis.windows.net/powerbi/api/Workspace.Read.All",
]

TOKEN_CACHE_PATH = Path.home() / ".powerbi_mcp_token_cache.json"


class PowerBIAuth:
    """Handles OAuth token acquisition and caching for Power BI API access."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.cache = msal.SerializableTokenCache()

        if TOKEN_CACHE_PATH.exists():
            self.cache.deserialize(TOKEN_CACHE_PATH.read_text())

        self.app = msal.PublicClientApplication(
            client_id,
            authority=AUTHORITY,
            token_cache=self.cache,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_cache(self) -> None:
        if self.cache.has_state_changed:
            TOKEN_CACHE_PATH.write_text(self.cache.serialize())
            TOKEN_CACHE_PATH.chmod(0o600)  # owner-only read/write

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_token_silent(self) -> str | None:
        """Return a valid access token from cache, or None if unavailable."""
        accounts = self.app.get_accounts()
        if not accounts:
            return None

        result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            self._save_cache()
            return result["access_token"]

        return None

    def initiate_device_flow(self) -> dict:
        """
        Start a device code flow.

        Returns the flow dict from MSAL which includes:
          - ``message``   – human-readable instructions with the URL and code
          - ``user_code`` – the code the user enters at the URL
          - ``expires_at``
        """
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(
                f"Failed to initiate device flow: {flow.get('error_description', flow)}"
            )
        # Persist the flow so complete_device_flow can use it later
        self._pending_flow = flow
        return flow

    def complete_device_flow(self, flow: dict) -> str:
        """
        Block until the user completes browser authentication, then return the token.
        Raises RuntimeError on failure.
        """
        result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(
                f"Authentication failed: {result.get('error_description', result)}"
            )
        self._save_cache()
        return result["access_token"]

    def clear_cache(self) -> None:
        """Remove cached tokens (forces re-authentication on next call)."""
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
        self.cache = msal.SerializableTokenCache()
        self.app = msal.PublicClientApplication(
            self.client_id,
            authority=AUTHORITY,
            token_cache=self.cache,
        )
