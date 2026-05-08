"""
Configuration — loads Azure AD credentials from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Azure AD + Graph API settings."""

    TENANT_ID: str = os.getenv("AZURE_TENANT_ID", "")
    CLIENT_ID: str = os.getenv("AZURE_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("AZURE_CLIENT_SECRET", "")
    ORGANIZER_USER_ID: str = os.getenv("ORGANIZER_USER_ID", "")

    # Microsoft identity / Graph endpoints
    AUTHORITY: str = f"https://login.microsoftonline.com/{TENANT_ID}"
    GRAPH_API_BASE: str = "https://graph.microsoft.com/v1.0"
    SCOPES: list[str] = ["https://graph.microsoft.com/.default"]


settings = Settings()