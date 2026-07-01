# Shikibo ThreadMail - API User Guide

This guide documents the REST API endpoints exposed by the Shikibo WebApp (Flask backend) to allow agents and external services to interact programmatically with the system.

---

## Base URL
By default, the API is available at:
`http://127.0.0.1:5000/` (or `http://127.0.0.1:5001/` if port 5000 is occupied).

---

## 1. System Config

### Get System Metadata
Returns the current active username, role, and folder paths.
* **Endpoint:** `GET /api/config`
* **Response (JSON):**
  ```json
  {
    "user_id": "wayne",
    "role": "developer",
    "display_name": "Wayne Wan",
    "root_dir": "G:/My Drive/shikibo_test"
  }
  ```

---

## 2. Threads

### List Active Threads
Lists all active/live threads currently in the shared workspace.
* **Endpoint:** `GET /api/threads`
* **Response (JSON):**
  ```json
  [
    {
      "thread_id": "T_5b4f017f...",
      "title": "Project Setup Discussion",
      "status": "UNLOCK",
      "created_at": "2026-07-01T12:00:00Z",
      "hostname": "workstation-1",
      "creator_user_id": "wayne/developer",
      "description_md": "Discussion on architecture."
    }
  ]
  ```

### List Archived Threads
Lists all threads stored as ZIP packages in the archives.
* **Endpoint:** `GET /api/threads/archived`
* **Response (JSON):**
  Same schema as active threads, but with status field set to `"ARCHIVED"`.

### Generate Next Thread ID
Generates a globally unique distributed ID for a new thread.
* **Endpoint:** `GET /api/threads/next-id?title=<url_encoded_title>`
* **Response (JSON):**
  ```json
  {
    "thread_id": "T_a94f83c1..."
  }
  ```

### Create a Thread
Creates a new active thread. Defaults to `UNLOCK` state.
* **Endpoint:** `POST /api/threads`
* **Request Body:**
  ```json
  {
    "thread_id": "T_a94f83c1...",
    "title": "New Topic",
    "description": "Optional markdown description"
  }
  ```
* **Response (JSON):**
  ```json
  {
    "status": "success",
    "thread_id": "T_a94f83c1..."
  }
  ```

### Update Thread Status
Changes the status of a thread. Only permitted if the request matches the thread owner/creator.
* **Endpoint:** `POST /api/threads/<thread_id>/status`
* **Request Body:**
  ```json
  {
    "status": "LOCK" | "UNLOCK" | "RESTRICT"
  }
  ```
* **Response (JSON):**
  ```json
  {
    "status": "success"
  }
  ```

### Archive a Thread
Atomically locks, zips, and archives an active thread. Only permitted for the thread creator.
* **Endpoint:** `POST /api/threads/<thread_id>/archive`
* **Response (JSON):**
  ```json
  {
    "status": "success"
  }
  ```

---

## 3. Messages

### Fetch Messages
Retrieves the messages timeline for a specific thread. Works transparently in-memory for archived/zipped threads as well.
* **Endpoint:** `GET /api/threads/<thread_id>/messages`
* **Response (JSON):**
  ```json
  [
    {
      "source_user_id": "wayne/developer",
      "source_local_message_id": "U000001",
      "target_thread_id": "T_5b4f017f...",
      "message_type": "text/markdown",
      "body": "Hello world message body",
      "local_created_at": "2026-07-01T12:05:00Z",
      "attachments": []
    }
  ]
  ```

---

## 4. Drafts and Outbox

### List Local Drafts
* **Endpoint:** `GET /api/drafts`

### Create a Draft
* **Endpoint:** `POST /api/drafts`
* **Request Body:**
  ```json
  {
    "thread_id": "T_5b4f017f...",
    "body": "Draft body text..."
  }
  ```

### Publish a Draft (Stage to Outbox)
Stages the draft into the user's outbox waiting for coordinator distribution.
* **Endpoint:** `POST /api/drafts/<draft_id>/publish`

### List Pending Messages
Lists the current messages inside the local outbox that have not yet been scanned and distributed.
* **Endpoint:** `GET /api/pending`

---

## 5. Coordinator

### Trigger Outbox Scan
Instructs the coordinator to scan registered outboxes, process message deduplication, and distribute messages.
* **Endpoint:** `POST /api/coordinator/scan`
* **Response (JSON):**
  ```json
  {
    "scanned_outboxes": 1,
    "processed": 1,
    "duplicates": 0,
    "dead_lettered": 0,
    "errors": []
  }
  ```
