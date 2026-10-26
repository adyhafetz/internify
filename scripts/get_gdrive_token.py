"""
One-time script to authorize Google Drive & Sheets OAuth2 access.

Usage:
1. Ensure your OAuth 2.0 Client Credentials (client_secret.json or client ID/Secret) are ready.
2. Run: python scripts/get_gdrive_token.py
3. It will open a browser window for you to sign in with your Google account.
4. It will save 'token.json' and print your GOOGLE_REFRESH_TOKEN to paste into .env.
"""

from __future__ import annotations
import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def main():
    client_secret_file = os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json")

    if not os.path.exists(client_secret_file):
        print(f"Error: Could not find '{client_secret_file}'.")
        print("Please download your OAuth client credentials JSON from Google Cloud Console")
        print("and save it as 'client_secret.json' in the root directory.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=8080)

    # Save token.json
    with open("token.json", "w") as token:
        token.write(creds.to_json())

    print("\n✅ Authorization successful!")
    print("Saved credentials to 'token.json'.")
    if creds.refresh_token:
        print("\n--- You can also add this directly to your .env file: ---")
        print(f"GOOGLE_CLIENT_ID={creds.client_id}")
        print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
        print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
        print("--------------------------------------------------------\n")


if __name__ == "__main__":
    main()

