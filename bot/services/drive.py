from __future__ import annotations
import logging
import os
from typing import Optional
from bot import config
from bot.services.google_auth import get_google_credentials

logger = logging.getLogger(__name__)

_drive_service = None


def _get_drive_service():
    global _drive_service
    if _drive_service is not None:
        return _drive_service

    creds = get_google_credentials()
    if not creds:
        logger.warning("No Google credentials configured (OAuth2 or Service Account). PDF uploads will be skipped.")
        return None

    try:
        from googleapiclient.discovery import build
        _drive_service = build("drive", "v3", credentials=creds)
        logger.info("Initialized Google Drive client with unified Google credentials.")
        return _drive_service
    except Exception as e:
        logger.error("Failed to initialize Google Drive service: %s", e)
        return None


def upload_to_drive(file_path: str, destination_name: str) -> Optional[str]:
    """
    Uploads a local PDF to the configured Google Drive folder and returns the webViewLink.
    Returns None if Drive is not configured or if upload fails.
    """
    service = _get_drive_service()
    if not service or not os.path.exists(file_path):
        return None

    try:
        from googleapiclient.http import MediaFileUpload

        file_metadata = {
            "name": destination_name,
        }
        if config.GOOGLE_DRIVE_FOLDER_ID:
            file_metadata["parents"] = [config.GOOGLE_DRIVE_FOLDER_ID]

        media = MediaFileUpload(file_path, mimetype="application/pdf", resumable=True)
        uploaded = (
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )

        file_id = uploaded.get("id")
        web_link = uploaded.get("webViewLink")

        # Attempt to make it viewable by anyone with the link
        try:
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()
        except Exception as perm_err:
            logger.debug("Permission sharing notice (might already be inherited): %s", perm_err)

        logger.info("Successfully uploaded %s to Google Drive: %s", destination_name, web_link)
        return web_link or f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as e:
        logger.error("Failed to upload file to Google Drive: %s", e)
        return None

