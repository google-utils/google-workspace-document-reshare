from googleapiclient.discovery import build
import json
from time import sleep
from datetime import datetime
import os
from config import get_sa_creds, MAX_FOLDER_LEVEL, sensitive_drives, specific_folder_id

# Global variable for log file
log_file = None

def log_message(message, level=0):
    """Simple logging to both file and console"""
    global log_file
    indent = "  " * level
    formatted_message = f"{indent}{message}"
    print(formatted_message, flush=True)
    if log_file:
        log_file.write(formatted_message + "\n")
        log_file.flush()

def load_mapping():
    """Load user mapping from mapping.json file"""
    with open('mapping.json', 'r', encoding='utf-8') as f:
        return json.load(f)
    

def get_sa_drive_service():
    """Get Drive API service using SA credentials"""
    sa_creds = get_sa_creds(f"./sa/service_account.json")
    return build('drive', 'v3', credentials=sa_creds)

def list_shared_drives(service):
    """List all shared drives accessible by the SA"""
    drives = []
    page_token = None
    while True:
        response = service.drives().list(pageToken=page_token).execute()
        drives.extend(response.get('drives', []))
        page_token = response.get('nextPageToken')
        if not page_token:
            break
    return drives

def list_drive_files(service, drive_id, parent_id=None):
    """List files/folders in a shared drive or folder. If parent_id is None, list root items of the drive."""
    files = []
    page_token = None
    
    # For shared drives:
    # - If parent_id is None or equals drive_id, we want root level items
    # - Otherwise, we want items in the specific folder
    if parent_id is None or parent_id == drive_id:
        q = f"'root' in parents or '{drive_id}' in parents"
    else:
        q = f"'{parent_id}' in parents"
    
    # Add trashed=false to exclude deleted items
    q += " and trashed = false"
    # print(f"Query: {q}")  # Debug logging
    
    while True:
        try:
            response = service.files().list(
                driveId=drive_id,
                corpora="drive",  # Important for Shared Drives
                includeItemsFromAllDrives=True,  # Required to search Shared Drives
                supportsAllDrives=True,  # Required for writing/modifying in Shared Drives
                pageSize=1000,
                q=q,
                fields="nextPageToken, files(id, name, mimeType, parents, driveId, size, modifiedTime)",
                orderBy="name",
                pageToken=page_token
            ).execute()
            
            # Filter files to ensure they belong to the correct drive
            drive_files = [f for f in response.get('files', []) if f.get('driveId') == drive_id]
            files.extend(drive_files)
            # print(f"Found {len(drive_files)} files in this batch")  # Debug logging
            
            page_token = response.get('nextPageToken')
            if not page_token:
                break
        except Exception as e:
            log_message(f"Error listing files for drive {drive_id}: {str(e)}", 1)
            break
            
    return files

def get_folder_permissions(service, folder_id):
    """Get permissions for a specific folder"""
    try:
        permissions = service.permissions().list(
            fileId=folder_id,
            supportsAllDrives=True,
            fields="permissions(id,type,role,emailAddress,domain)"
        ).execute()
        return permissions.get('permissions', [])
    except Exception as e:
        log_message(f"Error getting permissions for folder {folder_id}: {str(e)}", 1)
        return []

def deal_with_domain_sharing(service, folder_id, domain_permission, permissions, indent=""):
    """Handle domain sharing by adding allcompany@seconddomain.com with the same permissions.
    Returns True if domain sharing was found and handled, False otherwise.
    Skips if allcompany@seconddomain.com already has the same permissions.
    """
    try:
        target_email = 'allcompany@seconddomain.com'
        target_role = domain_permission['role']
        
        # Check if allcompany already has this permission
        for perm in permissions:
            if perm.get('emailAddress') == target_email and perm.get('role') == target_role:
                # log_message(f"{indent}  - Skipping domain share - {target_email} already has {target_role} access", 0)
                return True
        
        # If we get here, we need to add the permission
        log_message(f"{indent}  ! Found domain sharing for domain:{domain_permission['domain']} ({target_role})", 0)
        share_item(service, folder_id, target_email, target_role, indent)
        return True
        
    except Exception as e:
        log_message(f"Error handling domain sharing: {str(e)}", 1)
        return False



def deal_with_user_sharing(service, folder_id, user_permission, permissions, mapping_data, indent=""):
    """Handle user email migration by adding new email if old one is found in mapping.
    Returns True if user was found in mapping and sharing was handled, False otherwise.
    """
    try:
        user_email = user_permission['emailAddress']
        user_role = user_permission['role']
        
        # Check if this email is in mapping as an old address
        for mapping in mapping_data:
            user_name = mapping['old'].split('@')[0]
            migration_email = f"{user_name}@seconddomain.com"
            temp_email = f"{user_name}@seconddomain-temp.com"

            # first check if old user is in mapping
            if mapping['old'] == user_email:
                new_email = mapping['new']
                
                # Check if new email already has this permission
                for perm in permissions:
                    if perm.get('emailAddress') == new_email and perm.get('role') == user_role:
                        # new user already has exctly same permission
                        return True
                    if perm.get('emailAddress') == new_email and  perm.get('role') != user_role:
                        # new user already here but has different permission
                        role_hierarchy = {
                            'reader': 1,
                            'commenter': 2, 
                            'writer': 3,
                            'fileOrganizer': 4,
                            'organizer': 5
                        }
                        # If existing role has higher permissions, skip adding lower role
                        if role_hierarchy.get(perm.get('role'), 0) > role_hierarchy.get(user_role, 0):
                            log_message(f"{indent}  ! User {user_email} already has {perm.get('role')} permission, skipping {user_role} permission", 0)
                            return True

                    if perm.get('emailAddress') == temp_email and perm.get('role') == user_role:
                        # user's temp address already has exctly same permission
                        return True
                

                # If we get here, we need to add the permission
                log_message(f"{indent}  ! Found old email {user_email}, sharing with {new_email} ({user_role})", 0)
                share_item(service, folder_id, new_email, user_role, indent)
                return True
            
            # then check if the migration email is in mapping
            if migration_email == user_email:
                new_email = mapping['new']
                
                # Check if new email already has this permission
                
                for perm in permissions:
                    if perm.get('emailAddress') == new_email and perm.get('role') == user_role:
                        # new user already has exctly same permission
                        return True
                        
                    if perm.get('emailAddress') == new_email and  perm.get('role') != user_role:
                        # new user already here but has different permission
                        role_hierarchy = {
                            'reader': 1,
                            'commenter': 2, 
                            'writer': 3,
                            'fileOrganizer': 4,
                            'organizer': 5
                        }
                        # If existing role has higher permissions, skip adding lower role
                        if role_hierarchy.get(perm.get('role'), 0) > role_hierarchy.get(user_role, 0):
                            log_message(f"{indent}  ! User {user_email} already has {perm.get('role')} permission, skipping {user_role} permission", 0)
                            return True

                    if perm.get('emailAddress') == temp_email and perm.get('role') == user_role:
                        # user's temp address already has exctly same permission
                        return True
                
                # If we get here, we need to add the permission
                
                log_message(f"{indent}  !! Found migration email {user_email}, sharing with {new_email} ({user_role})", 0)
                share_item(service, folder_id, new_email, user_role)
                return True
        
        return False  # Email not found in mapping
        
    except Exception as e:
        log_message(f"Error handling user sharing: {str(e)}", 1)
        return False

def validate_folder_id(service, file_id, target_folder_id=specific_folder_id):
    """
    Validates if a file/folder is either the target folder or is contained within it at any depth.
    Returns True if valid, False otherwise.
    """
    try:
        current_id = file_id
        while current_id:
            # If we found our target folder, the path is valid
            if current_id == target_folder_id:
                return True
                
            # Get the file/folder metadata to check its parents
            file_metadata = service.files().get(
                fileId=current_id,
                supportsAllDrives=True,
                fields='parents'
            ).execute()
            
            # Get the parent ID (if any)
            parents = file_metadata.get('parents', [])
            if not parents:
                return False
                
            # Move up to the parent
            current_id = parents[0]
            
        return False
    
    except Exception as e:
        log_message(f"Error validating folder ID: {str(e)}", 1)
        return False

def review_permissions_on_files(service, drive_id, parent_id=None, level=1, path=""):
    """Review permissions for all files (non-folders) in a drive recursively"""
    try:
        # Don't process beyond MAX_FOLDER_LEVEL
        if level > MAX_FOLDER_LEVEL:
            return

        # Load mapping data once
        mapping_data = load_mapping()
        
        files = list_drive_files(service, drive_id, parent_id)
        
        # Filter to get only non-folder files
        files_list = [f for f in files if f['mimeType'] != 'application/vnd.google-apps.folder']
        total_files = len(files_list)
        
        if total_files > 0:
            indent = "  " * level
            log_message(f"{indent}Found {total_files} files at level {level}", 0)
            
            for idx, file in enumerate(files_list, 1):
                # Validate if file is in target folder, but only if an ID is specified
                if specific_folder_id and not validate_folder_id(service, file['id']):
                    continue

                # Build the file path for display
                current_path = f"{path}/{file['name']}" if path else file['name']
                
                # Print file being processed with progress
                log_message(f"{indent}[{idx}/{total_files}] Processing file [level {level}] \"{file['name']}\"", 0)
                
                # Get and print permissions
                permissions = get_folder_permissions(service, file['id'])
                if permissions:
                    for p in permissions:
                        if p.get('emailAddress'):
                            print(permissions)
                            deal_with_user_sharing(service, file['id'], p, permissions, mapping_data, indent)
                        elif p.get('domain'):
                            deal_with_domain_sharing(service, file['id'], p, permissions, indent)
                else:
                    log_message(f"{indent}  No explicit permissions", 0)
        
        # Now process files in subfolders
        folders = [f for f in files if f['mimeType'] == 'application/vnd.google-apps.folder']
        for folder in folders:
            # Only process subfolders that are within our target folder, if an ID is specified
            if specific_folder_id and not validate_folder_id(service, folder['id']):
                continue
            
            # Process files in this subfolder
            review_permissions_on_files(service, drive_id, folder['id'], level + 1, f"{path}/{folder['name']}")
            
    except Exception as e:
        log_message(f"Error reviewing file permissions: {str(e)}", 1)

def review_permissions_on_folders(service, drive_id, parent_id=None, level=1, path=""):
    """Review permissions for all folders in a drive recursively"""
    try:
        # Don't process beyond MAX_FOLDER_LEVEL
        if level > MAX_FOLDER_LEVEL:
            return

        # Load mapping data once
        mapping_data = load_mapping()
        
        files = list_drive_files(service, drive_id, parent_id)
        
        # Filter to get only folders
        folders = [f for f in files if f['mimeType'] == 'application/vnd.google-apps.folder']
        total_folders = len(folders)
        
        for idx, folder in enumerate(folders, 1):
            # Validate if folder is target folder or within it, but only if an ID is specified
            if specific_folder_id and not validate_folder_id(service, folder['id']):
                continue

            # Build the folder path for display
            current_path = f"{path}/{folder['name']}" if path else folder['name']
            
            # Print folder being processed with progress
            indent = "  " * level
            log_message(f"{indent}[{idx}/{total_folders}] Processing folder [level {level}] \"{folder['name']}\"", 0)
            
            # Get and print permissions
            permissions = get_folder_permissions(service, folder['id'])
            if permissions:
                perm_list = []
                for p in permissions:
                    if p.get('emailAddress'):
                        deal_with_user_sharing(service, folder['id'], p, permissions, mapping_data, indent)
                        perm_list.append(f"{p['emailAddress']} ({p['role']})")
                    elif p.get('domain'):
                        deal_with_domain_sharing(service, folder['id'], p, permissions, indent)
                        perm_list.append(f"domain:{p['domain']} ({p['role']})")
                    else:
                        perm_list.append(f"{p['type']}:{p['role']}")
            else:
                log_message(f"{indent}  No explicit permissions", 0)
            
            # Process subfolders
            review_permissions_on_folders(service, drive_id, folder['id'], level + 1, current_path)
            
    except Exception as e:
        log_message(f"Error reviewing permissions: {str(e)}", 1)

def share_item(service, item_id, email, role, indent=""):
    """Share a file or folder with specified email and role.
    
    Args:
        service: Google Drive service instance
        item_id: ID of the file or folder to share
        email: Email address to share with
        role: Role to grant (reader, writer, commenter, fileOrganizer, organizer)
        indent: Current indentation level for logging
    """
    try:
        # Create the permission body
        permission = {
            'type': 'user',
            'role': role,
            'emailAddress': email
        }
        
        # Create the permission without sending notification email
        result = service.permissions().create(
            fileId=item_id,
            body=permission,
            supportsAllDrives=True,
            sendNotificationEmail=False
        ).execute()
        
        log_message(f"{indent}  + Shared item {item_id} with {email} ({role})", 0)
        return result
    except Exception as e:
        log_message(f"{indent}  - Error sharing item {item_id} with {email}: {str(e)}", 0)
        return None

def review_permissions_on_drive(service, drive_id, drive_name):
    """Review and handle permissions for the shared drive itself"""
    try:
        # Load mapping data once
        mapping_data = load_mapping()
        
        log_message(f"Checking drive permissions for \"{drive_name}\"")
        
        if "@" in drive_name:
            log_message(f"Skipping {drive_name} drive permissions check.", 0)
            # we skipping this types of drives these are personal and should be already shared
            return
        permissions = get_folder_permissions(service, drive_id)
        if permissions:
            for p in permissions:
                if p.get('emailAddress'):
                    deal_with_user_sharing(service, drive_id, p, permissions, mapping_data, "")
                # elif p.get('domain'):
                #     deal_with_domain_sharing(service, drive_id, p, permissions, "")
            log_message(f"Done.", 0)
        else:
            log_message("  No explicit permissions on drive", 0)
            
    except Exception as e:
        log_message(f"Error reviewing drive permissions: {str(e)}", 1)

def main():
    global log_file
    if not os.path.exists('logs'):
        os.makedirs('logs')
    log_filename = f"logs/z-shared_drives_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file = open(log_filename, "w")
    try:
        # 1. Get shared drives
        service = get_sa_drive_service()
        drives = list_shared_drives(service)
        total_drives = len(drives)
        log_message(f"Found {total_drives} shared drives accessible by SA")
        
        # 2. Process each drive
        for idx, drive in enumerate(drives, 1):
            # Double check if drive is sensitive
            if drive['id'] in [d['id'] for d in sensitive_drives]:
                log_message(f"\n[{idx}/{total_drives}] Skipping sensitive drive \"{drive['name']}\" (ID: {drive['id']})")
                continue
            if drive['name'] in [d['name'] for d in sensitive_drives]:
                log_message(f"\n[{idx}/{total_drives}] Skipping sensitive drive \"{drive['name']}\" (ID: {drive['id']})")
                continue

            if "@" in drive['name']:
                continue

            # Optional: restrict processing to a single drive while testing.
            # if not drive.get('id') == "YOUR_SHARED_DRIVE_ID":
            #     continue

            log_message(f"\n[{idx}/{total_drives}] Processing drive \"{drive['name']}\" (ID: {drive['id']})")
            
            # 3. Review permissions on the drive itself first
            review_permissions_on_drive(service, drive['id'], drive['name'])

            # 4. Review permissions for all folders and files
            log_message("\nProcessing folders:")
            review_permissions_on_folders(service, drive['id'])
            
            log_message("\nProcessing files:")
            # review_permissions_on_files(service, drive['id'])
            
    except KeyboardInterrupt:
        log_message("\nGracefully stopping shared drive processing...")
    except Exception as e:
        log_message(f"Error: {str(e)}")
    finally:
        if log_file:
            log_file.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Main function already handles KeyboardInterrupt
        pass
    except Exception as e:
        print(f"Fatal error: {str(e)}")  # Print to console since log file might be closed 