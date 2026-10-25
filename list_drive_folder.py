#!/usr/bin/env python3
"""List all items under a Google Drive folder by name."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from application_service.google_auth import access_token_value, token_from_environment
from application_service.google_drive_artifacts import folder_id_from_url


def list_drive_folder(folder_name, token_source, root_folder_id=None):
    """Find a folder by name and list all its contents recursively."""
    access_token = access_token_value(token_source)
    headers = {"Authorization": "Bearer " + access_token}

    def request(method, url, payload=None):
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", "Bearer " + access_token)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=payload, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as e:
            print(f"HTTP Error: {e.code} - {e.reason}", file=sys.stderr)
            raise

    # Search everywhere in Drive, including shared drives
    query = urllib.parse.quote(
        f"name contains '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        safe=""
    )
    url = f"https://www.googleapis.com/drive/v3/files?q={query}&includeItemsFromAllDrives=true&supportsAllDrives=true&fields=files(id,name,mimeType,parents,webViewLink,modifiedTime,driveId)"
    result = request("GET", url)
    folders = result.get("files", [])

    if not folders:
        print(f"Folder '{folder_name}' not found in Drive.")
        return

    target_folder = folders[0]
    print(f"Found folder: {target_folder['name']}")
    print(f"Folder ID: {target_folder['id']}")
    print(f"Link: {target_folder.get('webViewLink', 'N/A')}")
    print(f"Modified: {target_folder.get('modifiedTime', 'N/A')}")
    print()

    # List all children recursively
    def list_children(parent_id, indent=0):
        query = urllib.parse.quote(
            f"'{parent_id}' in parents and trashed = false",
            safe=""
        )
        url = f"https://www.googleapis.com/drive/v3/files?q={query}&pageSize=1000&fields=files(id,name,mimeType,parents,webViewLink,modifiedTime,size)"
        result = request("GET", url)
        files = result.get("files", [])

        # Sort: folders first, then files, alphabetically
        files.sort(key=lambda f: (f["mimeType"] != "application/vnd.google-apps.folder", f["name"].lower()))

        for item in files:
            is_folder = item["mimeType"] == "application/vnd.google-apps.folder"
            prefix = "📁 " if is_folder else "📄 "
            size_str = f" ({int(int(item.get('size', 0)) / 1024)} KB)" if not is_folder and item.get("size") else ""
            print(f"{'  ' * indent}{prefix}{item['name']}{size_str}")

            if is_folder:
                list_children(item["id"], indent + 1)

    print(f"Contents of '{folder_name}':")
    print("=" * 60)
    list_children(target_folder["id"])


if __name__ == "__main__":
    # Load runtime config for the root folder
    runtime_path = os.path.join(os.path.dirname(__file__), "config", "runtime.json")
    root_folder_id = None
    if os.path.exists(runtime_path):
        with open(runtime_path) as f:
            runtime = json.load(f)
        artifact_root = runtime.get("google_drive", {}).get("artifact_root_url")
        if artifact_root:
            from application_service.google_drive_artifacts import folder_id_from_url
            root_folder_id = folder_id_from_url(artifact_root)

    token = token_from_environment()
    if not token:
        print("Error: No Google credentials found. Set GOOGLE_ACCESS_TOKEN or refresh token credentials.", file=sys.stderr)
        sys.exit(1)

    folder_name = sys.argv[1] if len(sys.argv) > 1 else "JobSearch2026"
    list_drive_folder(folder_name, token, root_folder_id)
