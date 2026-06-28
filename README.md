# itracker - Distributed ThreadMail System (v1.0 VIP)

`itracker` is a low-cost, cloud-folder-based asynchronous coordination system for humans and AI agents. It lets multiple participants collaborate through durable, topic-oriented message threads using ordinary filesystem operations on a shared sync folder (e.g., Google Drive, Dropbox, iCloud, or a local directory) as the transport layer.

## Core Features
*   **Asymmetric Mail Sorting**: Participants write immutable message packages into their own private outbox. A single global coordinator service scans outboxes, sorts, and distributes messages into thread folders.
*   **Deduplication & Idempotency**: The coordinator processes messages based on a compound key (`source_user_id + source_local_message_id`) recorded in a local SQLite ledger, ensuring no message is lost or duplicated.
*   **Minimalist Local WebApp**: A clean, intuitive local browser interface for managing active threads, reading message timelines, drafting posts, attaching multimodal files, and tracking outbox receipts.
*   **Timed Coordinator Service**: Run the coordinator daemon to auto-scan sync outboxes periodically, or invoke single one-shot scans.

---

## Getting Started

### Prerequisites
*   Python 3.10 or 3.11

### Installation & Setup

1.  **Create a Virtual Environment**:
    ```powershell
    python -m venv .venv
    .venv\Scripts\Activate.ps1   # On Windows
    source .venv/bin/activate    # On macOS/Linux
    ```

2.  **Install Dependencies**:
    ```bash
    python -m pip install -r requirements.txt
    ```

3.  **Default Storage Configuration**:
    By default, `itracker` targets `G:\My Drive\itracker_test` as the sync database root. You can modify this in `src/config.py` or by passing the `-r`/`--root-dir` parameter to the CLI.

---

## Usage

All commands are orchestrated via `main.py`:

### 1. Launch the WebApp
Launches the minimalist user interface and automatically opens the default web browser:
```powershell
.venv\Scripts\python main.py webapp
```

### 2. Run a One-Shot Outbox Scan
Scan all registered outboxes and distribute pending messages immediately:
```powershell
.venv\Scripts\python main.py scan
```

### 3. Run the Coordinator Daemon
Runs the coordinator as a background service that runs a scan periodically (interval configured in `src/config.py`):
```powershell
.venv\Scripts\python main.py service
```

### 4. Archive a Thread
Create a ZIP package of a closed thread:
```powershell
.venv\Scripts\python main.py archive <thread_id>
```

---

## Testing & Verification

Run the automated integration suite:
```powershell
.venv\Scripts\python tests/test_flow.py
```
This tests client-initialization, drafts, attachments, outbox publications, coordinator scans, receipts, SQLite ledger integrity, and duplicate prevention.
