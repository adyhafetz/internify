"""
One-time script to authorize Google Drive & Sheets OAuth2 access.

Usage (on your own computer, not the server: it needs a browser):
1. Download your OAuth client (type "Desktop app") from Google Cloud Console and save it as client_secret.json here.
2. Run: python scripts/get_gdrive_token.py
3. Sign in, allow access, and paste the three printed lines into the .env on the server.
See docs/domain_setup.md, part 3.
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
    # port=0: any free port (8080 may be the dashboard). offline + consent: always hand back a refresh token.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

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
        print("Keep these private. Delete client_secret.json and token.json from this computer when you have copied them.")
    else:
        print("Google did not return a refresh token. Remove Internify at https://myaccount.google.com/permissions and run this again.")


if __name__ == "__main__":
    main()

