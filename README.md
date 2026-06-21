## Info
Re-share Google Drive content from old to new user emails during a Google Workspace
domain migration, driven by a mapping of `old -> new` addresses. Two entrypoints:

- **`document_manager.py`** — users' **personal Drive**: for every user it walks their
  owned files/folders and re-applies sharing per the mapping. Writes summary stats to
  PostgreSQL.
- **`shared_drives_manager.py`** — **Shared Drives**: walks every (non-sensitive)
  shared drive and re-applies folder/file sharing per the mapping (also handles
  domain-wide shares).

Both share the same `config.py` and `mapping.json`.

## How to use
1. Put your Service Account (SA) JSON key at `app/sa/service_account.json`
2. Edit `config.ws_data` (and optional `c_level_emails`) — your domains/admins
3. Copy `app/mapping.json.example` to `app/mapping.json` and fill in the `old -> new` email pairs
4. Provide DB settings via `.env` (copy from `env.example`)

## How to start

### Using Python directly
1. Ensure you have Python installed.
2. Install the required dependencies:
   ```
   pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib psycopg2-binary
   ```
3. Run a script:
   ```
   python app/document_manager.py        # personal Drive
   python app/shared_drives_manager.py   # Shared Drives
   ```

### Using Docker Compose (Recommended)
1. Ensure you have Docker and Docker Compose installed.
2. Build and start the services (PostgreSQL and the app):
   ```
   docker-compose up --build
   ```
   This will start both the database and the application. Logs will be available in the `logs` directory.
3. To stop the services, press `Ctrl+C` and then run:
   ```
   docker-compose down
   ```

## What each file does
- **document_manager.py**: personal-Drive entrypoint. Processes each user's owned
  documents and folders and updates sharing based on the mapping.
- **shared_drives_manager.py**: Shared-Drives entrypoint. Walks shared drives and
  updates folder/file sharing based on the mapping; skips drives listed in
  `config.sensitive_drives`.

### Important to know
Here are the Domain Wide Delegation scopes you must have configured for your Service Account client id in G-Suite:
```text
https://www.googleapis.com/auth/admin.directory.user.readonly
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/drive.readonly
https://www.googleapis.com/auth/drive.file
https://www.googleapis.com/auth/drive
```
Also, in order to get it done, <b>Google Sheets API</b> must be enabled as well as <b>Google Admin SDK</b> if you want to retrieve users automatically.