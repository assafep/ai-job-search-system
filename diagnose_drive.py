#!/usr/bin/env python3
"""Diagnostic script to pinpoint the exact 403 error in Drive provisioning."""
import json
import sys
import subprocess
import urllib.request
import urllib.error

def check_gcloud_token():
    """Check if gcloud has a valid access token."""
    print("=" * 60)
    print("1. Checking gcloud access token...")
    try:
        result = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"   ❌ Failed to get gcloud access token: {result.stderr.strip()}")
            return None
        token = result.stdout.strip()
        print(f"   ✓ Access token obtained: {token[:20]}...")
        return token
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

def check_token_scopes(token):
    """Check what scopes the token has."""
    print("=" * 60)
    print("2. Checking token scopes...")
    try:
        url = f'https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={token}'
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
        scopes = data.get('scope', '').split()
        print(f"   Token scopes: {scopes}")
        drive_scope = 'https://www.googleapis.com/auth/drive'
        drive_file_scope = 'https://www.googleapis.com/auth/drive.file'
        if drive_scope in scopes:
            print(f"   ✓ Full Drive scope present")
            return True
        elif drive_file_scope in scopes:
            print(f"   ⚠ Only drive.file scope present (may not be sufficient)")
            return False
        else:
            print(f"   ❌ No Drive scope found")
            return False
    except Exception as e:
        print(f"   ❌ Error checking scopes: {e}")
        return False

def check_folder_access(token, path):
    """Check if we can access the specified folder path."""
    print("=" * 60)
    print("3. Checking folder access...")
    try:
        # Split path
        parts = []
        for piece in str(path).replace('\\', '/').split('/'):
            piece = piece.strip()
            if not piece or piece.lower() == 'my drive':
                continue
            parts.append(piece)
        
        print(f"   Path parts: {parts}")
        
        # Build API request
        req = urllib.request.Request(
            'https://www.googleapis.com/drive/v3/files',
            headers={'Authorization': f'Bearer {token}'}
        )
        
        # Try to find the folder
        parent = 'root'
        current_folder = None
        for i, name in enumerate(parts):
            escaped_name = name.replace('\\', '\\\\').replace("'", "\\'")
            query = f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent}' in parents and trashed = false"
            params = f'q={urllib.parse.quote(query)}&fields=nextPageToken,files(id,name,parents,capabilities)&supportsAllDrives=false'
            
            url = f'https://www.googleapis.com/drive/v3/files?{params}'
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
            
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.load(response)
                files = data.get('files', [])
                if not files:
                    # List siblings
                    sibling_query = f"'{parent}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                    sibling_params = f'q={urllib.parse.quote(sibling_query)}&fields=files(id,name)&supportsAllDrives=false'
                    sibling_url = f'https://www.googleapis.com/drive/v3/files?{sibling_params}'
                    sibling_req = urllib.request.Request(sibling_url, headers={'Authorization': f'Bearer {token}'})
                    with urllib.request.urlopen(sibling_req, timeout=30) as response:
                        sibling_data = json.load(response)
                    siblings = [f['name'] for f in sibling_data.get('files', [])]
                    print(f"   ❌ Folder '{name}' not found. Siblings: {siblings}")
                    return False
                current_folder = files[0]
                print(f"   ✓ Found folder '{name}' (id: {current_folder['id']})")
                print(f"     Capabilities: {current_folder.get('capabilities', {})}")
                parent = current_folder['id']
            except urllib.error.HTTPError as e:
                detail = e.read().decode('utf-8', 'replace')[:300]
                print(f"   ❌ HTTP {e.code} when searching for '{name}': {detail}")
                return False
        
        # Check final folder capabilities
        if current_folder:
            folder_id = current_folder['id']
            cap_url = f'https://www.googleapis.com/drive/v3/files/{folder_id}?fields=capabilities&supportsAllDrives=false'
            cap_req = urllib.request.Request(cap_url, headers={'Authorization': f'Bearer {token}'})
            with urllib.request.urlopen(cap_req, timeout=30) as response:
                cap_data = json.load(response)
            caps = cap_data.get('capabilities', {})
            print(f"   Final folder capabilities:")
            print(f"     canAddChildren: {caps.get('canAddChildren')}")
            print(f"     canEdit: {caps.get('canEdit')}")
            print(f"     canShare: {caps.get('canShare')}")
            
            if not caps.get('canAddChildren'):
                print(f"   ❌ Cannot add children to this folder")
                return False
            if not caps.get('canEdit'):
                print(f"   ❌ Cannot edit this folder")
                return False
            if not caps.get('canShare'):
                print(f"   ⚠ Cannot share this folder (may not be able to grant service account access)")
                return False
            return True
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_service_account_permissions(token, folder_id):
    """Check if we can grant permissions to a service account."""
    print("=" * 60)
    print("4. Checking service account permission grant...")
    try:
        # Get current permissions
        perm_url = f'https://www.googleapis.com/drive/v3/files/{folder_id}/permissions?fields=permissions(id,emailAddress,role)&supportsAllDrives=false'
        perm_req = urllib.request.Request(perm_url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(perm_req, timeout=30) as response:
            perm_data = json.load(response)
        permissions = perm_data.get('permissions', [])
        print(f"   Current permissions ({len(permissions)}):")
        for perm in permissions[:10]:  # Show first 10
            print(f"     {perm.get('emailAddress', 'N/A')} ({perm.get('role')}): {perm.get('id')}")
        
        # Try to create a test permission (we'll delete it)
        print(f"   Testing permission creation...")
        test_email = 'test@example.com'  # Dummy email for testing
        create_url = f'https://www.googleapis.com/drive/v3/files/{folder_id}/permissions?supportsAllDrives=false&sendNotificationEmail=false'
        create_data = json.dumps({
            'type': 'user',
            'role': 'writer',
            'emailAddress': test_email
        }).encode()
        create_req = urllib.request.Request(
            create_url,
            data=create_data,
            method='POST',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }
        )
        try:
            with urllib.request.urlopen(create_req, timeout=30) as response:
                perm = json.load(response)
            print(f"   ✓ Successfully created test permission (id: {perm.get('id')})")
            # Clean up
            delete_url = f'https://www.googleapis.com/drive/v3/files/{folder_id}/permissions/{perm.get("id")}?supportsAllDrives=false'
            delete_req = urllib.request.Request(delete_url, method='DELETE', headers={'Authorization': f'Bearer {token}'})
            try:
                with urllib.request.urlopen(delete_req, timeout=30):
                    pass
                print(f"   ✓ Cleaned up test permission")
            except Exception as e:
                print(f"   ⚠ Failed to clean up test permission: {e}")
            return True
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:300]
            print(f"   ❌ HTTP {e.code} when creating permission: {detail}")
            return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    import urllib.parse
    path = "My Drive/JobSearch/JobSearch 2026"
    
    print(f"Diagnostic for Drive path: {path}")
    print()
    
    # Step 1: Get token
    token = check_gcloud_token()
    if not token:
        print("\n❌ Cannot proceed without a valid gcloud access token.")
        sys.exit(1)
    
    # Step 2: Check scopes
    has_full_drive = check_token_scopes(token)
    if not has_full_drive:
        print("\n⚠ Token may not have full Drive scope. Try: gcloud auth login --enable-gdrive-access --brief")
    
    # Step 3: Check folder access
    folder_accessible = check_folder_access(token, path)
    if not folder_accessible:
        print("\n❌ Cannot access the specified folder.")
        sys.exit(1)
    
    # Extract folder_id from the last successful check
    # We need to re-run the folder resolution to get the ID
    parts = []
    for piece in str(path).replace('\\', '/').split('/'):
        piece = piece.strip()
        if not piece or piece.lower() == 'my drive':
            continue
        parts.append(piece)
    
    parent = 'root'
    folder_id = None
    for name in parts:
        escaped_name = name.replace('\\', '\\\\').replace("'", "\\'")
        query = f"name = '{escaped_name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent}' in parents and trashed = false"
        params = f'q={urllib.parse.quote(query)}&fields=files(id)&supportsAllDrives=false'
        url = f'https://www.googleapis.com/drive/v3/files?{params}'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
        if data.get('files'):
            folder_id = data['files'][0]['id']
            parent = folder_id
    
    if folder_id:
        # Step 4: Check service account permissions
        can_grant = check_service_account_permissions(token, folder_id)
        if not can_grant:
            print("\n❌ Cannot grant permissions to service account on this folder.")
            print("   This is likely the source of your 403 error.")
            sys.exit(1)
    
    print("\n" + "=" * 60)
    print("✓ All checks passed! The issue may be elsewhere.")
    print("=" * 60)

if __name__ == '__main__':
    main()
