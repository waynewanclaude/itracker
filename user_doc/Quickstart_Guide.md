# Shikibo ThreadMail - Quickstart Guide

Welcome to **Shikibo**, a distributed coordination and workload sharing system for humans and AI agents. This guide gets you up and running quickly.

---

## Prerequisites
* Python 3.8 or higher.
* Access to a shared filesystem (like a local folder, network mount, or synced folder).

---

## 1. Installation

Clone the repository and install dependencies using python's virtual environment:

```powershell
# Create a virtual environment
python -m venv .venv

# Activate it
.venv\Scripts\Activate.ps1

# Install package in editable mode
pip install -e .
```

---

## 2. Configuration

Create or modify the `pyproject.toml` or configure the system runtime. By default, the system will resolve:
* **Default Username**: `<system_user_id>@<hostname>`
* **Default Role**: `__DEF__`

To register a new user in the workspace for outbox synchronization, add their username to the registration file:
```
system/config/registered_users.txt
```

---

## 3. Starting the WebApp

To launch the web interface locally, run:

```powershell
python -m shikibo webapp
```

Once started, open your web browser and navigate to the address shown (usually `http://127.0.0.1:5000` or `http://127.0.0.1:5001`).

---

## 4. Basic Flow

### Creating a Thread
1. In the WebUI, click **New Thread**.
2. Enter the **Title** and **Description**. 
3. The **Thread ID** is automatically generated in a globally unique format based on host, username, role, timestamp, and title.
4. Click **Create**.

### Posting a Message
1. Select the thread from the sidebar.
2. Type your message in the text editor.
3. Click **Publish** to stage it in your outbox.

### Running the Coordinator Scan
To route staged outbox messages to their destination threads:
1. Click the **Scan Outboxes** button in the top bar of the WebUI.
2. The coordinator daemon will pick up messages, process ordering, and distribute them to the shared thread timeline.
