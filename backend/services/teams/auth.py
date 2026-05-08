"""
Authentication — acquires an access token from Azure AD using the
client-credentials flow (app-only, no user sign-in required).
"""

import msal
from .config import settings


class GraphAuthProvider:
    """Manages token acquisition via MSAL ConfidentialClientApplication."""

    def __init__(self):
        self._app = None

    def _get_msal_app(self):
        """Lazily initialise the MSAL app so the module can be imported
        even before the .env file is filled in."""
        if self._app is None:
            if not settings.TENANT_ID or not settings.CLIENT_ID or not settings.CLIENT_SECRET:
                raise RuntimeError(
                    "Azure AD credentials are not configured. "
                    "Copy .env.example to .env and fill in AZURE_TENANT_ID, "
                    "AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET."
                )
            self._app = msal.ConfidentialClientApplication(
                client_id=settings.CLIENT_ID,
                client_credential=settings.CLIENT_SECRET,
                authority=settings.AUTHORITY,
            )
        return self._app

    def get_access_token(self) -> str:
        """
        Return a valid access token.
        MSAL caches tokens automatically, so repeated calls won't hit Azure AD
        until the token is close to expiry.
        """
        app = self._get_msal_app()

        # Try the token cache first
        result = app.acquire_token_silent(settings.SCOPES, account=None)

        if not result:
            # No cached token — request a new one
            result = app.acquire_token_for_client(scopes=settings.SCOPES)

        if "access_token" in result:
            return result["access_token"]

        error_desc = result.get("error_description", result.get("error", "Unknown error"))
        raise RuntimeError(f"Failed to acquire token: {error_desc}")


# Singleton
auth_provider = GraphAuthProvider()