from google.oauth2 import service_account


# Workspaces to process: domain -> admin to impersonate (domain-wide delegation).
ws_data = {
    "yourdomain.com": {
        "domain": "yourdomain.com",
        "admin": "admin@yourdomain.com",
    },
    # "seconddomain.com": {
    #     "domain": "seconddomain.com",
    #     "admin": "admin@seconddomain.com",
    # },
}


# Optional allowlist for special-cased accounts (e.g. executives) — placeholders.
c_level_emails = [
    "exec1@yourdomain.com",
    "exec1@seconddomain.com",
    "exec2@yourdomain.com",
    "exec2@seconddomain.com",
]


# --- Shared Drives reshare settings (used by shared_drives_manager.py) ---

# How deep to recurse into folder trees inside a shared drive.
MAX_FOLDER_LEVEL = 2

# If set to a folder id, only that folder (and its subtree) is processed.
specific_folder_id = None

# Drives to skip entirely (by id and/or name) — fill with your own.
sensitive_drives = [
    {
        "id": "YOUR_DRIVE_ID_TO_SKIP",
        "name": "Example Drive To Skip"
    },
]

def get_admin_creds(sa_path, admin_email):
    credentials = service_account.Credentials.from_service_account_file(
        sa_path,
        scopes=[
            # 'https://www.googleapis.com/auth/admin.directory.user',
            'https://www.googleapis.com/auth/admin.directory.user.readonly',
            # 'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/drive.readonly',
            'https://www.googleapis.com/auth/spreadsheets',
        ],
        subject=admin_email
    )
    return credentials


def get_sa_creds(service_account_file):
    """
    Returns service account credentials for Google API.
    """
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.file',
    ]
    creds = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
    return creds


def get_user_creds(sa_path, user_email):
    credentials = service_account.Credentials.from_service_account_file(
        sa_path,
        scopes=['https://www.googleapis.com/auth/drive'],
        subject=user_email
    )
    return credentials
