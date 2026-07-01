# Shikibo ThreadMail - Administration Guide

This guide describes how to configure, secure, and maintain the Shikibo ThreadMail system.

---

## 1. System Directories Layout

All data is stored in the workspace path under `system/`:

```
system/
├── archive/         # Zipped thread packages (*.zip)
├── config/          # Global configuration (registered users, coordinator host lock)
├── coordinator/     # SQL ledger database (ledger.db) and PID locks
├── threads/         # Active thread folders
└── users/           # User outboxes, drafts, and receipts folders
```

---

## 2. Managing Registered Users

To prevent unauthorized file writes and data pollution, the coordinator only monitors registered users' outboxes.

* **Configuration File:** `system/config/registered_users.txt`
* **Format:** One username per line (e.g. `wayne`, `admin_agent`, `test_runner`).
* **Note:** For users with active roles, their outbox directories (e.g., `system/users/wayne/developer/outbox/`) are automatically detected and scanned as long as their primary username (`wayne`) is registered.

---

## 3. Coordinator Locking & Security

The coordinator runs as a single service across the shared filesystem. To prevent race conditions and multiple coordinators running in parallel, Shikibo implements two mechanisms:

### Authorized Host lock
The coordinator restricts itself to a specific machine and OS user.
* **Configuration File:** `system/config/coordinator_host.json`
* **Format:**
  ```json
  {
    "host": "your-computer-name",
    "user": "your-os-username"
  }
  ```
* **Failure mode:** If run from a mismatched machine or user, the coordinator writes an error log to `system/coordinator/<hostname>-<pid>.txt` and exits immediately.

### Process Locking (PID lock)
Active coordinator processes claim execution ownership using a PID lock file:
* **Lock File:** `system/coordinator/coordinator_pid.txt`
* **Behavior:** When starting, the coordinator checks if the PID listed in this file is still actively running on the system. If it is, the new process exits to avoid overlapping scans.

---

## 4. Command Line Interface (CLI)

The `shikibo` package exposes several command line commands.

### Start the WebApp Dev Server
Launches the Flask API backend and serves the WebUI files.
```powershell
python -m shikibo webapp
```

### Trigger a Single Outbox Scan
Performs a one-off scan of all registered outboxes, processes deduplication, distributes messages, and terminates.
```powershell
python -m shikibo scan
```

### Run the Coordinator Daemon
Runs the outbox scanner loop indefinitely (polls every 5 seconds).
```powershell
python -m shikibo daemon
```

### Archive a Thread Manually
Archives a thread to a zip file in `system/archive/` and deletes the active directory folder.
```powershell
python -m shikibo archive <thread_id>
```
