from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
from time import sleep
from datetime import datetime
import os
from config import get_admin_creds, get_user_creds, ws_data, c_level_emails
import psycopg2

# Global variable for log file
log_file = None

def log_message(message, level=0):
    """Simple logging to both file and console"""
    global log_file
    
    indent = "  " * level
    formatted_message = f"{indent}{message}"
    
    # Print to console
    print(formatted_message, flush=True)
    
    # Write to file
    if log_file:
        log_file.write(formatted_message + "\n")
        log_file.flush()  # Ensure immediate write

def load_mapping():
    """Load user mapping from mapping.json file"""
    with open('mapping.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def get_all_users(workspace_data):
    """Returns all users from workspace"""
    all_users = []
    admin_creds = get_admin_creds(f"./sa/service_account.json", workspace_data["admin"])
    service = build('admin', 'directory_v1', credentials=admin_creds)
    request = service.users().list(customer='my_customer', query='isSuspended=false', maxResults=500)
    while request is not None:
        response = request.execute()
        all_users.extend(response.get('users', []))
        request = service.users().list_next(request, response)
    return all_users

def get_permissions_for_files(service, files):
    """Fetch and attach permissions for a list of files (documents or folders)"""
    for file in files:
        try:
            permissions = service.permissions().list(fileId=file['id'], fields='permissions(emailAddress,role)').execute()
            file_permissions = permissions.get('permissions', [])
            file['permissions'] = [{'email': p['emailAddress'], 'role': p['role']} for p in file_permissions if 'emailAddress' in p]
        except Exception as e:
            log_message(f"Error getting permissions for {file['name']}: {str(e)}", 3)
            file['permissions'] = []
    return files

def get_folder_hierarchy(workspace_data, user_email):
    """Get the complete folder hierarchy for a user. Returns a tuple of (all_folders, top_level_ids, second_level_ids, third_level_ids, fourth_level_ids, fifth_level_ids)"""
    user_creds = get_user_creds(f"./sa/service_account.json", user_email)
    service = build('drive', 'v3', credentials=user_creds)

    # Get all folders owned by the user
    query = "'me' in owners and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
    fields = "nextPageToken, files(id, name, mimeType, parents)"
    all_folders = []
    page_token = None

    while True:
        try:
            results = service.files().list(q=query, fields=fields, pageToken=page_token).execute()
        except HttpError as e:
            if e.resp.status == 401:
                log_message(f"401 Unauthorized for user {user_email}: {str(e)}", 2)
                return [], set(), set(), set(), set(), set(), set()
            else:
                raise
        items = results.get('files', [])
        for item in items:
            folder = {
                'id': item['id'],
                'name': item['name'],
                'mimeType': item['mimeType'],
                'parent': item.get('parents', [None])[0]
            }
            all_folders.append(folder)
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    # Build folder hierarchy
    owned_folder_ids = set(f['id'] for f in all_folders)
    top_level_folders = [f for f in all_folders if f['parent'] not in owned_folder_ids]
    top_level_ids = set(f['id'] for f in top_level_folders)
    
    second_level_folders = [f for f in all_folders if f['parent'] in top_level_ids]
    second_level_ids = set(f['id'] for f in second_level_folders)
    
    third_level_folders = [f for f in all_folders if f['parent'] in second_level_ids]
    third_level_ids = set(f['id'] for f in third_level_folders)
    
    fourth_level_folders = [f for f in all_folders if f['parent'] in third_level_ids]
    fourth_level_ids = set(f['id'] for f in fourth_level_folders)
    
    fifth_level_folders = [f for f in all_folders if f['parent'] in fourth_level_ids]
    fifth_level_ids = set(f['id'] for f in fifth_level_folders)

    return all_folders, top_level_ids, second_level_ids, third_level_ids, fourth_level_ids, fifth_level_ids

def get_owned_folders(workspace_data, user_email, level=None, folder_hierarchy=None):
    """Returns a list of folders owned by the user for a specific level (1=top, 2=second, 3=third, 4=fourth, 5=fifth).
    If level is None, returns all folders up to 5 levels deep."""
    if folder_hierarchy is None:
        all_folders, top_level_ids, second_level_ids, third_level_ids, fourth_level_ids, fifth_level_ids = get_folder_hierarchy(workspace_data, user_email)
    else:
        all_folders, top_level_ids, second_level_ids, third_level_ids, fourth_level_ids, fifth_level_ids = folder_hierarchy

    # Find folders for the specified level
    if level == 1:
        folders = [f for f in all_folders if f['id'] in top_level_ids]
        log_message(f"Found {len(folders)} top-level folders. Retrieving permissions...", 3)
        return folders
    elif level == 2:
        folders = [f for f in all_folders if f['id'] in second_level_ids]
        log_message(f"Found {len(folders)} second-level folders. Retrieving permissions...", 3)
        return folders
    elif level == 3:
        folders = [f for f in all_folders if f['id'] in third_level_ids]
        log_message(f"Found {len(folders)} third-level folders. Retrieving permissions...", 3)
        return folders
    elif level == 4:
        folders = [f for f in all_folders if f['id'] in fourth_level_ids]
        log_message(f"Found {len(folders)} fourth-level folders. Retrieving permissions...", 3)
        return folders
    elif level == 5:
        folders = [f for f in all_folders if f['id'] in fifth_level_ids]
        log_message(f"Found {len(folders)} fifth-level folders. Retrieving permissions...", 3)
        return folders
    
    # If no level specified, return all folders
    folders_to_process = [f for f in all_folders if f['id'] in (top_level_ids | second_level_ids | third_level_ids | fourth_level_ids | fifth_level_ids)]
    log_message(f"Found {len(folders_to_process)} total folders across all levels. Retrieving permissions...", 1)
    return folders_to_process

def get_owned_documents(workspace_data, user_email, level=None, folder_hierarchy=None):
    """Returns a list of files owned by the user for a specific level (0=root, 1=first level folders, 2=second level, 3=third level, 4=fourth level, 5=fifth level).
    If level is None, returns all files up to 5 levels deep."""
    user_creds = get_user_creds(f"./sa/service_account.json", user_email)
    service = build('drive', 'v3', credentials=user_creds)

    if folder_hierarchy is None:
        _, top_level_ids, second_level_ids, third_level_ids, fourth_level_ids, fifth_level_ids = get_folder_hierarchy(workspace_data, user_email)
    else:
        _, top_level_ids, second_level_ids, third_level_ids, fourth_level_ids, fifth_level_ids = folder_hierarchy

    documents = []
    fields = "nextPageToken, files(id, name, mimeType, parents)"
    
    # Get files from root (level 0)
    if level is None or level == 0:
        query = "'me' in owners and trashed = false and mimeType != 'application/vnd.google-apps.folder' and 'root' in parents"
        page_token = None

        while True:
            try:
                results = service.files().list(q=query, fields=fields, pageToken=page_token).execute()
            except HttpError as e:
                if e.resp.status == 401:
                    log_message(f"401 Unauthorized for user {user_email}: {str(e)}", 2)
                    return []
                else:
                    raise
            items = results.get('files', [])
            documents.extend([{
                'id': item['id'],
                'name': item['name'],
                'mimeType': item['mimeType'],
                'parent': item.get('parents', [None])[0]
            } for item in items])
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        
        if level == 0:
            log_message(f"Found {len(documents)} files in root. Retrieving permissions...", 3)
            return documents

    # Get files from specific folder levels
    target_folder_ids = []
    if level == 1:
        target_folder_ids = list(top_level_ids)
    elif level == 2:
        target_folder_ids = list(second_level_ids)
    elif level == 3:
        target_folder_ids = list(third_level_ids)
    elif level == 4:
        target_folder_ids = list(fourth_level_ids)
    elif level == 5:
        target_folder_ids = list(fifth_level_ids)
    elif level is None:
        target_folder_ids = list(top_level_ids | second_level_ids | third_level_ids | fourth_level_ids | fifth_level_ids)

    if target_folder_ids:
        for folder_id in target_folder_ids:
            query = f"'me' in owners and trashed = false and mimeType != 'application/vnd.google-apps.folder' and '{folder_id}' in parents"
            page_token = None

            while True:
                try:
                    results = service.files().list(q=query, fields=fields, pageToken=page_token).execute()
                except HttpError as e:
                    if e.resp.status == 401:
                        log_message(f"401 Unauthorized for user {user_email}: {str(e)}", 2)
                        continue
                    else:
                        raise
                items = results.get('files', [])
                documents.extend([{
                    'id': item['id'],
                    'name': item['name'],
                    'mimeType': item['mimeType'],
                    'parent': item.get('parents', [None])[0]
                } for item in items])
                page_token = results.get('nextPageToken')
                if not page_token:
                    break

    if level is not None:
        log_message(f"Found {len(documents)} files in level {level}. Retrieving permissions...", 3)
    else:
        root_files = sum(1 for doc in documents if doc['parent'] == 'root')
        folder_files = len(documents) - root_files
        log_message(f"Found {root_files} files in root and {folder_files} files in folders (up to 5 levels deep)", 1)
    
    return documents

def share_document(workspace_data, file_id, user_email, role, owner_email):
    """Share document with user without sending notification"""
    # Use owner's credentials to share the document
    owner_creds = get_user_creds(f"./sa/service_account.json", owner_email)
    service = build('drive', 'v3', credentials=owner_creds)
    
    user_permission = {
        'type': 'user',
        'role': role,
        'emailAddress': user_email
    }
    
    try:
        service.permissions().create(
            fileId=file_id,
            body=user_permission,
            sendNotificationEmail=False,
            fields='id'
        ).execute()
        return True
    except Exception as e:
        log_message(f"Error sharing document {file_id} with {user_email}: {str(e)}", 2)
        return False

def process_user_documents(workspace_data, owner_email, mapping, documents=None):
    """Process all documents owned by a user and update sharing based on mapping"""
    # log_message(f"Processing: {owner_email}", 1)
    
    # Get all documents owned by the user (если не переданы)
    if documents is None:
        documents = get_owned_documents(workspace_data, owner_email)

    for doc in documents:
        current_sharing = [p['email'] for p in doc['permissions']]
        
        for perm in doc['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
            
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Sharing '{doc['name']}' with self. Add {matching_mapping['new']} as 'writer'", 4)
                    share_document(workspace_data, doc['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                log_message(f"Sharing document '{doc['name']}' with {matching_mapping['new']} (role: {role})", 3)
                share_document(workspace_data, doc['id'], matching_mapping['new'], role, owner_email)
                continue

            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
               
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        # If role is owner, downgrade to writer
                        if role == 'owner':
                            log_message(f"Sharing '{doc['name']}' with self. Add {email_to_share} as 'writer'", 4)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing document '{doc['name']}' with {email_to_share} (role: {role_to_share})", 3)
                        share_document(workspace_data, doc['id'], email_to_share, role_to_share, owner_email)
                        break
                        
                    # Skip if already shared with the new email
                    if email_to_share in current_sharing:
                        continue
                    
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 4)
                    log_message(f"! Sharing document '{doc['name']}' with {email_to_share} (role: {role})", 4)
                    share_document(workspace_data, doc['id'], email_to_share, role, owner_email)
                    break

def process_user_folders(workspace_data, owner_email, mapping, folders=None):
    """Process all folders owned by a user and update sharing based on mapping"""
    if folders is None:
        folders = get_owned_folders(workspace_data, owner_email)

    # Build folder structure
    folder_id_to_name = {folder['id']: folder['name'] for folder in folders}
    folder_id_to_parent = {folder['id']: folder.get('parent') for folder in folders}
    
    # Get user credentials for permission requests
    user_creds = get_user_creds(f"./sa/service_account.json", owner_email)
    service = build('drive', 'v3', credentials=user_creds)

    def get_folder_path(folder):
        path_parts = [folder['name']]
        parent_id = folder.get('parent')
        while parent_id:
            parent_name = folder_id_to_name.get(parent_id)
            if not parent_name:
                break
            path_parts.insert(0, parent_name)
            parent_id = folder_id_to_parent.get(parent_id)
        return '/'.join(path_parts)

    # Get root level folders (no parent in our list)
    root_folders = [f for f in folders if f['parent'] not in folder_id_to_name]
    log_message(f"Processing root level folders... (found {len(root_folders)})", 2)
    
    # Get and process permissions for root level
    root_folders_with_perms = get_permissions_for_files(service, root_folders)
    for folder in root_folders_with_perms:
        if folder['name'] == "My Computer":
            continue
            
        current_sharing = [p['email'] for p in folder['permissions']]
        folder_path = get_folder_path(folder)
        
        for perm in folder['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
                
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"Sharing with self. Add {matching_mapping['new']} as 'writer'", 5)
                    share_document(workspace_data, folder['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                
            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        if role == 'owner':
                            log_message(f"Folder: {folder_path}", 4)
                            if email_to_share in current_sharing:
                                break
                            log_message(f"Sharing with self. Add {email_to_share} as 'writer'", 5)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        log_message(f"Folder: {folder_path}", 4)
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing with {email_to_share} (role: {role_to_share})", 5)
                        share_document(workspace_data, folder['id'], email_to_share, role_to_share, owner_email)
                        break
                    if email_to_share in current_sharing:
                        log_message(f"Folder: {folder_path}", 4)
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 5)
                    log_message(f"! Sharing with {email_to_share} (role: {role})", 5)
                    share_document(workspace_data, folder['id'], email_to_share, role, owner_email)
                    break

    sleep(2)

    # Get level 1 folders (parent is a root folder)
    root_ids = set(f['id'] for f in root_folders)
    level1_folders = [f for f in folders if f['parent'] in root_ids]
    log_message(f"Processing level 1 folders... (found {len(level1_folders)})", 2)
    
    # Get and process permissions for level 1
    level1_folders_with_perms = get_permissions_for_files(service, level1_folders)
    for folder in level1_folders_with_perms:
        if folder['name'] == "My Computer":
            continue
            
        current_sharing = [p['email'] for p in folder['permissions']]
        folder_path = get_folder_path(folder)
        
        for perm in folder['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
                
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"Sharing with self. Add {matching_mapping['new']} as 'writer'", 5)
                    share_document(workspace_data, folder['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                
            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        if role == 'owner':
                            log_message(f"Folder: {folder_path}", 4)
                            if email_to_share in current_sharing:
                                break
                            log_message(f"Sharing with self. Add {email_to_share} as 'writer'", 5)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        log_message(f"Folder: {folder_path}", 4)
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing with {email_to_share} (role: {role_to_share})", 5)
                        share_document(workspace_data, folder['id'], email_to_share, role_to_share, owner_email)
                        break
                    if email_to_share in current_sharing:
                        log_message(f"Folder: {folder_path}", 4)
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 5)
                    log_message(f"! Sharing with {email_to_share} (role: {role})", 5)
                    share_document(workspace_data, folder['id'], email_to_share, role, owner_email)
                    break

    sleep(2)

    # Get level 2 folders (parent is a level 1 folder)
    level1_ids = set(f['id'] for f in level1_folders)
    level2_folders = [f for f in folders if f['parent'] in level1_ids]
    log_message(f"Processing level 2 folders... (found {len(level2_folders)})", 2)
    
    # Get and process permissions for level 2
    level2_folders_with_perms = get_permissions_for_files(service, level2_folders)
    for folder in level2_folders_with_perms:
        if folder['name'] == "My Computer":
            continue
            
        current_sharing = [p['email'] for p in folder['permissions']]
        folder_path = get_folder_path(folder)
        
        for perm in folder['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
                
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"Sharing with self. Add {matching_mapping['new']} as 'writer'", 5)
                    share_document(workspace_data, folder['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                
            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        if role == 'owner':
                            log_message(f"Folder: {folder_path}", 4)
                            if email_to_share in current_sharing:
                                break
                            log_message(f"Sharing with self. Add {email_to_share} as 'writer'", 5)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        log_message(f"Folder: {folder_path}", 4)
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing with {email_to_share} (role: {role_to_share})", 5)
                        share_document(workspace_data, folder['id'], email_to_share, role_to_share, owner_email)
                        break
                    if email_to_share in current_sharing:
                        log_message(f"Folder: {folder_path}", 4)
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 5)
                    log_message(f"! Sharing with {email_to_share} (role: {role})", 5)
                    share_document(workspace_data, folder['id'], email_to_share, role, owner_email)
                    break

    sleep(2)

    # Get level 3 folders (parent is a level 2 folder)
    level2_ids = set(f['id'] for f in level2_folders)
    level3_folders = [f for f in folders if f['parent'] in level2_ids]
    log_message(f"Processing level 3 folders... (found {len(level3_folders)})", 2)
    
    # Get and process permissions for level 3
    level3_folders_with_perms = get_permissions_for_files(service, level3_folders)
    for folder in level3_folders_with_perms:
        if folder['name'] == "My Computer":
            continue
            
        current_sharing = [p['email'] for p in folder['permissions']]
        folder_path = get_folder_path(folder)
        
        for perm in folder['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
                
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"Sharing with self. Add {matching_mapping['new']} as 'writer'", 5)
                    share_document(workspace_data, folder['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                
            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        if role == 'owner':
                            log_message(f"Folder: {folder_path}", 4)
                            if email_to_share in current_sharing:
                                break
                            log_message(f"Sharing with self. Add {email_to_share} as 'writer'", 5)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        log_message(f"Folder: {folder_path}", 4)
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing with {email_to_share} (role: {role_to_share})", 5)
                        share_document(workspace_data, folder['id'], email_to_share, role_to_share, owner_email)
                        break
                    if email_to_share in current_sharing:
                        log_message(f"Folder: {folder_path}", 4)
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 5)
                    log_message(f"! Sharing with {email_to_share} (role: {role})", 5)
                    share_document(workspace_data, folder['id'], email_to_share, role, owner_email)
                    break

    sleep(2)

    # Get level 4 folders (parent is a level 3 folder)
    level3_ids = set(f['id'] for f in level3_folders)
    level4_folders = [f for f in folders if f['parent'] in level3_ids]
    log_message(f"Processing level 4 folders... (found {len(level4_folders)})", 2)
    
    # Get and process permissions for level 4
    level4_folders_with_perms = get_permissions_for_files(service, level4_folders)
    for folder in level4_folders_with_perms:
        if folder['name'] == "My Computer":
            continue
            
        current_sharing = [p['email'] for p in folder['permissions']]
        folder_path = get_folder_path(folder)
        
        for perm in folder['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
                
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"Sharing with self. Add {matching_mapping['new']} as 'writer'", 5)
                    share_document(workspace_data, folder['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                
            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        if role == 'owner':
                            log_message(f"Folder: {folder_path}", 4)
                            if email_to_share in current_sharing:
                                break
                            log_message(f"Sharing with self. Add {email_to_share} as 'writer'", 5)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        log_message(f"Folder: {folder_path}", 4)
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing with {email_to_share} (role: {role_to_share})", 5)
                        share_document(workspace_data, folder['id'], email_to_share, role_to_share, owner_email)
                        break
                    if email_to_share in current_sharing:
                        log_message(f"Folder: {folder_path}", 4)
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 5)
                    log_message(f"! Sharing with {email_to_share} (role: {role})", 5)
                    share_document(workspace_data, folder['id'], email_to_share, role, owner_email)
                    break

    sleep(2)

    # Get level 5 folders (parent is a level 4 folder)
    level4_ids = set(f['id'] for f in level4_folders)
    level5_folders = [f for f in folders if f['parent'] in level4_ids]
    log_message(f"Processing level 5 folders... (found {len(level5_folders)})", 2)
    
    # Get and process permissions for level 5
    level5_folders_with_perms = get_permissions_for_files(service, level5_folders)
    for folder in level5_folders_with_perms:
        if folder['name'] == "My Computer":
            continue
            
        current_sharing = [p['email'] for p in folder['permissions']]
        folder_path = get_folder_path(folder)
        
        for perm in folder['permissions']:
            shared_email = perm['email']
            role = perm['role']
            
            # NEW email check - skip if already in mapping
            matching_mapping = next((m for m in mapping if m['new'] == shared_email), None)
            if matching_mapping:
                continue
                
            # OLD email check
            matching_mapping = next((m for m in mapping if m['old'] == shared_email), None)
            if matching_mapping:
                if matching_mapping['new'] == owner_email:
                    continue
                if matching_mapping['new'] in current_sharing:
                    continue
                sleep(1)
                if matching_mapping['old'] == owner_email:
                    # Skip if already shared with the new email
                    if matching_mapping['new'] in current_sharing:
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"Sharing with self. Add {matching_mapping['new']} as 'writer'", 5)
                    share_document(workspace_data, folder['id'], matching_mapping['new'], 'writer', owner_email)
                    continue
                
            # DESTINATION DOMAIN CHECK
            for m in mapping:
                email_to_share = m['new']
                username = m['new'].split('@')[0]
                migration_email = f"{username}@seconddomain.com"
                if shared_email == migration_email:
                    if owner_email == migration_email:
                        if role == 'owner':
                            log_message(f"Folder: {folder_path}", 4)
                            if email_to_share in current_sharing:
                                break
                            log_message(f"Sharing with self. Add {email_to_share} as 'writer'", 5)
                            role_to_share = 'writer'
                        else:
                            role_to_share = role
                        log_message(f"Folder: {folder_path}", 4)
                        if email_to_share in current_sharing:
                            break
                        log_message(f"+ Sharing with {email_to_share} (role: {role_to_share})", 5)
                        share_document(workspace_data, folder['id'], email_to_share, role_to_share, owner_email)
                        break
                    if email_to_share in current_sharing:
                        log_message(f"Folder: {folder_path}", 4)
                        continue
                    log_message(f"Folder: {folder_path}", 4)
                    log_message(f"! Found migration email match: {shared_email} -> {email_to_share}", 5)
                    log_message(f"! Sharing with {email_to_share} (role: {role})", 5)
                    share_document(workspace_data, folder['id'], email_to_share, role, owner_email)
                    break

def write_to_psql(user_id, user_email, doc_count, folder_count):
    """Write user_id, user_email, document count, and folder count to PostgreSQL"""
    try:
        conn = psycopg2.connect(
            dbname='documents',
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT')
        )
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_documents (
                user_id TEXT,
                user_email TEXT,
                doc_count INTEGER,
                folder_count INTEGER,
                PRIMARY KEY (user_id)
            )
        ''')
        cur.execute('''
            INSERT INTO user_documents (user_id, user_email, doc_count, folder_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE 
            SET user_email = EXCLUDED.user_email, 
                doc_count = EXCLUDED.doc_count,
                folder_count = EXCLUDED.folder_count
        ''', (user_id, user_email, doc_count, folder_count))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        log_message(f"Error writing to PostgreSQL: {str(e)}", 2)

def main():
    global log_file
    
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.makedirs('logs')
    
    # Create log file once at start
    log_filename = f"logs/processing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file = open(log_filename, "w")
    
    try:
        # Load user mapping
        mapping = load_mapping()
        
        # Process each workspace
        for workspace_name, workspace_data in ws_data.items():
            log_message(f"Processing workspace: {workspace_name}")
            
            # Get all users in the workspace
            users = get_all_users(workspace_data)
            log_message(f"Found {len(users)} users in workspace", 0)
            
            # Define range of users to process
            start_index = 1
            end_index = 900
            users_to_process = users[start_index:end_index]
            
            log_message(f"Processing users from index {start_index} to {end_index} (out of {len(users)} total users)")
            
            # Process each user in the range
            for user_index, user in enumerate(users_to_process, start_index + 1):
                user_email = user['primaryEmail']
                try:
                    # skip C-Level
                    if any(email in user_email for email in c_level_emails):
                        continue

                    # Skip if user is suspended
                    if user.get('suspended', False):
                        continue

                    # Skip users who have never logged in
                    if user.get('changePasswordAtNextLogin'):
                        print(f"User {user_email} has never logged in")
                        continue

                    # skip accounts already on the destination domain
                    if "@seconddomain.com" in user_email:
                        continue

                    # skip specific heavy accounts with too many folders, if needed
                    # if "heavy.user" in user_email:
                    #     continue

                    log_message(f"[{user_index}/{end_index}] Processing: {user_email}", 1)
                    
                    try:
                        user_creds = get_user_creds(f"./sa/service_account.json", user_email)
                        service = build('drive', 'v3', credentials=user_creds)
                        
                        # Get folder hierarchy once for this user
                        folder_hierarchy = get_folder_hierarchy(workspace_data, user_email)
                        
                        # Process all folders (now handles levels internally)
                        folders = get_owned_folders(workspace_data, user_email, None, folder_hierarchy)
                        process_user_folders(workspace_data, user_email, mapping, folders)
                        
                        sleep(10)# sleep to allow files to inherit permissions from previously reshared folders
                        
                        # Process files level by level
                        for level in range(0, 6):  # 0=root, 1=first level, 2=second level, 3=third level, 4=fourth level, 5=fifth level
                            log_message(f"Processing level {level} files...", 2)
                            documents = get_owned_documents(workspace_data, user_email, level, folder_hierarchy)
                        documents = get_permissions_for_files(service, documents)
                        process_user_documents(workspace_data, user_email, mapping, documents)
                        
                        # Get total counts for database
                        all_documents = get_owned_documents(workspace_data, user_email, None, folder_hierarchy)
                        all_folders = get_owned_folders(workspace_data, user_email, None, folder_hierarchy)
                        log_message(f"Writing to PostgreSQL for user {user_email}", 1)
                        write_to_psql(user.get('id', user_email), user_email, len(all_documents), len(all_folders))
                    except HttpError as e:
                        if e.resp.status == 401:
                            log_message(f"401 Unauthorized for user {user_email}: {str(e)}", 2)
                            continue
                        else:
                            raise
                except Exception as e:
                    log_message(f"Error processing user {user_email}: {str(e)}", 2)
                    continue
    finally:
        # Close log file
        if log_file:
            log_file.close()

if __name__ == "__main__":
    try:
        main() 
    except KeyboardInterrupt:
        log_message("\nStopping document sharing...")
    except Exception as e:
        log_message(f"Error: {str(e)}")
        if log_file:
            log_file.close()