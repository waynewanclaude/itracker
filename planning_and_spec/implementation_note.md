# Advisory Implementation Notes & Design Rationale

This document outlines the software design decisions, technical architecture, and module choices for the **Distributed ThreadMail System** (`shikibo`).

---

## 1. Repository Structure

The physical layout of the repository matches the logical boundaries of the system:

```
shikibo/
|-- planning_and_spec/
|   |-- design_and_coding_principle.md  # Architectural guidelines
|   |-- development_governance.md       # Workflow constraints
|   |-- glossary.md                     # Terminology and end-user guide
|   |-- implementation_note.md          # [THIS FILE] Tech choices & rationale
|   |-- prime_directive.md              # Safety guidelines for files
|   `-- user_intent.md                  # Binding application behavior
|-- pyproject.toml                      # Package build configuration
|-- tests/
|   `-- test_flow.py                    # Integration test suite
`-- shikibo/
    |-- __init__.py
    |-- __main__.py                     # Entry point and CLI orchestrator
    |-- config.py                       # Configuration & path parsing (Pydantic)
    |-- storage.py                      # Abstraction layer for filesystem operations
    |-- client/
    |   |-- __init__.py
    |   `-- client.py                   # ThreadMail client (drafts, outbox publishing)
    |-- coordinator/
    |   |-- __init__.py
    |   `-- service.py                  # Background synchronization & distribution daemon
    `-- webapp/
        |-- __init__.py
        |-- app.py                      # Flask backend API
        `-- static/
            |-- app.js                  # Frontend client application logic
            |-- index.html              # Composer, timeline, and sidebar structure
            `-- style.css               # Vanilla CSS layout & typography styles
```

---

## 2. Core Technical Stack & Module Choices

*   **Pydantic (`shikibo/config.py`)**: Used for managing global configuration (`Settings`). Pydantic ensures clean validation of environment variables and JSON config overrides, and provides automated initialization hooks (`model_post_init`) to dynamically construct absolute folder paths based on overrides.
*   **Flask (`shikibo/webapp/app.py`)**: Powering the local WebApp interface. Flask was selected because it is lightweight, standard, requires minimal boilerplate, and is easy to orchestrate locally alongside background coordinator timers.
*   **SQLite3 (`shikibo/coordinator/service.py`)**: Embedded local cache (`coordinator_ledger.db`) for tracking distributed message deduplication keys. It enables the coordinator to perform fast, indexed database queries to check for duplicate messages rather than scanning all threads on every tick, while remaining completely reconstructible from the raw filesystem.
*   **Standard Python Filesystem Operations (`shikibo/storage.py`)**: File system actions are encapsulated behind `FileSystemStorage` using standard library modules like `os`, `shutil`, `tempfile`, and `pathlib`. It avoids external database or cloud provider SDKs, enabling `shikibo` to run seamlessly on top of any file sync provider (Google Drive, Dropbox, OneDrive, Syncthing, or local directories).

---

## 3. Design Rationale & Choices Made

### A. The Draft Staging System
Drafts are private, mutable workspaces stored locally (`drafts/<user_id>/`). When a message is in progress:
*   **Persistence**: If the WebApp or user environment crashes, their composition text and attachment listings are saved to disk (`body.md` + `draft.json`) and reloaded automatically.
*   **Attachment Isolation**: Attaching a file immediately copies it to the draft's folder. If the user moves, edits, or deletes the original source file on their machine before clicking "Publish", the publish operation does not fail because the staged copy remains intact.
*   **Atomic Compilation**: The draft folder serves as a safe workspace to build and validate the schema before copying it as a complete package into the synchronized cloud outbox.

### B. Two-Layer User & Role Folder Structure
To support structured collaboration, top-level users can have dynamically created sub-user roles (e.g. `test_user/developer`).
*   **Why Users Have Sub-Folders**:
    1.  **Organizational Division**: Allows a single participant to maintain different roles (e.g., project lead, reviewer, developer) with isolated drafts, outboxes, and receipts.
    2.  **Distributed Administration**: The coordinator only needs to register the top-level user (in `system/config/registered_users.txt`). Top-level users can create any roles they wish locally without requiring the coordinator to update its registered user configs.
    3.  **Security**: The coordinator validates that the user identity (e.g. `user_id/role`) matches the directory structure `users/user_id/role/outbox/` to enforce that users cannot publish messages under other users' folders or unregistered accounts.
*   **Directory Structure**:
    *   Top-Level: `users/<user_id>/outbox/` and `users/<user_id>/receipts/`
    *   Role-Level: `users/<user_id>/<role>/outbox/` and `users/<user_id>/<role>/receipts/`
*   **Flat Directory Message Mapping**:
    To prevent nested folders in thread messages which complicate list/sorting routines, slash characters in identity names are mapped to dots inside message directory names under threads:
    `20260628T110729Z_000002_human_wayne.developer_U000001`
    During ledger recovery, the coordinator parses the dot back into a slash (`human_wayne/developer`) to reconstruct correct ledger database rows.

### C. Cloud-Friendliness & No-Loss Integrity
*   **Instance Lock Mechanics**: While the system avoids file locking for regular thread access to minimize sync-churn on cloud sync providers, it enforces process-level exclusivity for the coordinator service via a two-tier startup validation:
    1.  **Host and User Authorization**: The coordinator reads `<root_dir>/system/config/coordinator_host.json` to verify that the executing host and system user match the authorized configuration. This prevents unauthorized systems or synced client instances from starting background scanning loops.
    2.  **Process PID Verification**: The coordinator reads `<root_dir>/system/coordinator/coordinator_pid.txt`. If the PID belongs to an active `shikibo` coordinator process, the new instance exits with a message. Otherwise, it updates the lock file with its own PID to claim the coordinator throne.
*   **Safe Sorther Execution**: The coordinator implements an atomic copy-and-verify workflow:
    1.  Verify outbox package is stable.
    2.  Copy package to thread folder.
    3.  Confirm verification.
    4.  Write receipt back to the user's receipt directory.
    5.  Record entry to SQLite ledger.
    6.  Move package to `.processed/` under the outbox.
*   **Idempotency**: All operations are deduplicated by `(source_user_id, source_local_message_id)`. If the coordinator crashes mid-process, it catches up idempotently on the next run without introducing duplicate posts.


### D. Multi-Project Directory Segmentation
To support multiple isolated development streams (e.g. `shikibo`, `ocrone`, `book_image_salon`), each project is assigned its own top-level directory root:
*   Each project acts as an entirely independent database, config, and sync workspace.
*   This isolates scanning overhead to active projects, preventing bloating and eliminating the risk of a failure in one project affecting other tasks.
*   To switch between project contexts, client services and coordinators are simply initialized pointing to the project's subfolder (e.g. by passing the subfolder path as `-r` / `--root-dir`).

### E. Context Extraction via Zip Attachment Service
When building new features, developers can attach zip archives of previous projects/development threads (e.g., `T_T123_ARCHIVE.zip`) as references at the beginning of a new project's coordination thread.
*   To read these archives uniformly, clients and agents unzip the attachments into a local, read-only temporary directory.
*   This allows both humans and AI models to inspect historical code structure, specs, and design context directly in their local filesystems, without bloating the active syncing folder layout.

### F. Directed Mentions & Agent Coordination Loops
To facilitate multi-agent task dispatching and collaboration:
*   **Mentions Extraction**: When publishing a message, the client library automatically scans the markdown body using the regex `@([\w/-]+)` to capture any user or role identities (e.g. `@human_wayne` or `@agent_developer/reviewer`) and includes them in the `mentions` list inside `message.json` metadata.
*   **Autonomous Sensing Loop (ReAct/MCP Pattern)**:
    1.  **Sense**: The agent processes active threads via the `list_active_threads` and `read_thread_messages` client APIs. It filters for open threads where its identity is mentioned in the latest message.
    2.  **Plan**: The agent checks the thread message history, unzips any attached historical contexts into local read-only folders, and loads the files into its context window.
    3.  **Act**: The agent performs coding, testing, or review tasks, saves its work locally, creates a draft, optionally includes files/zips, and publishes a reply pointing to the next recipient (e.g., mentioning `@human_user` to review or `@agent_reviewer` to double-check).
*   **MCP Integration**: Wrapping the `ThreadMailClient` behind an MCP (Model Context Protocol) server exposes these operations directly as LLM tools, allowing cognitive loops to participate seamlessly as standard thread actors.

### G. Multi-Agent Coordination Rules & Lifecycle
To establish a clear operational framework and avoid conflicts in the multi-agent system:
*   **Conflict Prevention (Bid-Approval Model)**: To prevent race conditions where multiple agents work on the same task simultaneously, the system uses a centralized bidding process:
    1.  The `agent_project_lead` identifies a task and tags candidate worker agents.
    2.  Interested agents publish an "Intent to Work" bid (e.g., `@agent_project_lead ready to claim`) and wait.
    3.  The `agent_project_lead` selects the appropriate agent (based on role specialization or availability), publishes a confirmation message approving the claim with a 1-sentence reasoning explanation (e.g., `Claim approved for @agent_backend_dev because the task primarily involves REST routing changes.`), and assigns it.
    4.  Only the approved agent starts working; other candidate agents stand down.
*   **Role Specialization (Personas)**: Rather than running redundant worker processes, agents are divided into specialized personas to keep system prompts lean and tasks accurate:
    *   *Developers*: `agent_backend_dev`, `agent_db_dev`, `agent_frontend_dev`, and `agent_ops_dev`.
    *   *Reviewers*: `agent_security_reviewer`, `agent_performance_reviewer`, and `agent_style_reviewer`.
*   **Thread Closure Authority**: We employ a "Suggester" model. Worker agents suggest archiving when work is complete (e.g. `@agent_project_lead task complete, suggest archive`). Only the human user (via WebApp) or the `agent_project_lead` has the authority to change the thread status to `DONE` and trigger the coordinator to compile the archive.
*   **Human-in-the-Loop Approvals**: Verification is text-based. When human users review a completed task, they reply to the thread with approval keywords (e.g., "Approve", "LGTM", "Verified"). The `agent_project_lead` monitors for these messages sent by the human user's ID, parses the approval, and marks the thread `DONE` automatically.
*   **Team Roster & Authorization Keys**: To ensure safety and prevent token waste, the team organization is managed statically via a filesystem config file (`system/config/roster.json`) rather than relying on LLM reasoning:
    *   **Structure**: The roster lists all active agents, their roles, and explicitly designates the identity holding the `TEAM_LEAD` role (which carries the authority to approve task claims and close threads).
    *   **Read-Only Access**: All agents read `system/config/roster.json` at startup to identify team roles, capabilities, and the authorized `TEAM_LEAD` identity.
    *   **Write-Protection**: Only the human user can modify the root `system/config/roster.json` file directly via the filesystem.
    *   **Sub-Agent Lifecycle**: The agent playing the `TEAM_LEAD` role can maintain its own "personnel" registry in its private local workspace and spin up/down worker sub-agents dynamically, but it is strictly prohibited from modifying the root `roster.json` or elevating any sub-agent to the `TEAM_LEAD` role.
    *   **Implicit Activation**: Spun up worker sub-agents do not publish thread log announcements or register in any central shared registry file when activated. Their presence is completely implicit; they simply begin reading the thread messages and posting bids when tasks require their attention, preventing thread log clutter.
*   **Multi-Agent Security & OS Delegation**: To avoid complex custom application security and leverage mature, robust boundaries, `shikibo` delegates security enforcement directly to the underlying Operating System's native permission model (POSIX permissions or NTFS ACLs):
    *   **No Custom Sandboxing Code**: The codebase does not implement virtual permission layers or virtual sandboxes. If running under a single shared system account or cloud sync folder, all agents share full read/write access.
    *   **Optional System-Level Isolation**: For users who require added security, the administrator manually configures native filesystem permissions:
        *   The coordinator process runs under a system account that owns and has write permissions over the thread directories (`system/threads/`), the index (`system/index/`), the archives (`system/archive/`), and global configs (`system/config/`).
        *   Each worker agent process runs under a separate system user account, granted write permissions *only* to its designated outbox (`users/<user_id>/<role>/outbox/`) and local draft (`drafts/<user_id>/<role>/`) directories. All other shared directories are configured as read-only.
