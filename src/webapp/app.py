import os
import json
import tempfile
import webbrowser
from threading import Timer
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from pathlib import Path

from src.config import load_settings
from src.storage import FileSystemStorage
from src.client.client import ThreadMailClient
from src.coordinator.service import CoordinatorService

app = Flask(__name__, static_folder="static", static_url_path="")

# Load global settings and components
settings = load_settings()
storage = FileSystemStorage()
client = ThreadMailClient(settings, storage)
coordinator = CoordinatorService(settings, storage)

# Automatically register our client's user identity in the coordinator list for easy local testing
coordinator.register_user(settings.user_id)

# Helper to secure serve attachments
@app.route("/api/attachments/<thread_id>/<msg_folder>/<filename>")
def serve_attachment(thread_id: str, msg_folder: str, filename: str):
    attachments_dir = Path(settings.thread_root) / thread_id / "messages" / msg_folder / "attachments"
    if not os.path.exists(attachments_dir):
        return "Attachment folder not found", 404
    return send_from_directory(directory=str(attachments_dir), path=filename)

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "user_id": settings.user_id,
        "display_name": settings.display_name,
        "root_dir": settings.root_dir,
        "local_draft_root": settings.local_draft_root,
        "outbox_root": settings.outbox_root,
        "receipt_root": settings.receipt_root,
        "thread_root": settings.thread_root,
        "index_root": settings.index_root,
        "archive_root": settings.archive_root,
        "scan_interval": settings.scan_interval
    })

@app.route("/api/users", methods=["GET"])
def list_users():
    return jsonify(coordinator.get_registered_users())

@app.route("/api/users", methods=["POST"])
def add_user():
    data = request.json or {}
    new_user_id = data.get("user_id")
    if not new_user_id:
        return jsonify({"error": "Missing user_id"}), 400
        
    new_user_id = secure_filename(new_user_id)
    if not new_user_id:
        return jsonify({"error": "Invalid user_id"}), 400
        
    try:
        coordinator.register_user(new_user_id)
        user_dir = Path(settings.root_dir) / "users" / new_user_id
        storage.makedirs(user_dir / "outbox")
        storage.makedirs(user_dir / "receipts")
        return jsonify({"status": "success", "user_id": new_user_id})
    except Exception as e:
        return jsonify({"error": f"Failed to add user: {e}"}), 500

@app.route("/api/threads", methods=["GET"])
def list_threads():
    return jsonify(client.list_active_threads())

@app.route("/api/threads", methods=["POST"])
def create_thread():
    data = request.json or {}
    thread_id = data.get("thread_id")
    title = data.get("title")
    description = data.get("description", "")
    
    if not thread_id or not title:
        return jsonify({"error": "Missing thread_id or title"}), 400
        
    thread_dir = Path(settings.thread_root) / thread_id
    if storage.exists(thread_dir):
        return jsonify({"error": "Thread already exists"}), 400
        
    storage.makedirs(thread_dir)
    storage.makedirs(thread_dir / "messages")
    
    # Save thread.json
    thread_meta = {
        "thread_id": thread_id,
        "title": title,
        "status": "OPEN",
        "created_at": storage.write_file_new(
            thread_dir / "thread.json",
            json.dumps({
                "thread_id": thread_id,
                "title": title,
                "status": "OPEN",
                "created_at": datetime_now()
            }, indent=2)
        )
    }
    
    # Save README.md as the description
    storage.write_file_new(thread_dir / "README.md", description)
    return jsonify({"status": "success", "thread_id": thread_id})

@app.route("/api/threads/<thread_id>/messages", methods=["GET"])
def get_messages(thread_id: str):
    return jsonify(client.read_thread_messages(thread_id))

@app.route("/api/threads/<thread_id>/done", methods=["POST"])
def mark_thread_done(thread_id: str):
    thread_meta_path = Path(settings.thread_root) / thread_id / "thread.json"
    if not storage.exists(thread_meta_path):
        return jsonify({"error": "Thread not found"}), 404
        
    try:
        meta = json.loads(storage.read_file_text(thread_meta_path))
        meta["status"] = "DONE"
        meta["closed_at"] = datetime_now()
        storage.delete(thread_meta_path)
        storage.write_file_new(thread_meta_path, json.dumps(meta, indent=2))
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": f"Failed to mark thread DONE: {e}"}), 500

@app.route("/api/drafts", methods=["GET"])
def list_drafts():
    return jsonify(client.list_drafts())

@app.route("/api/drafts", methods=["POST"])
def create_draft():
    data = request.json or {}
    thread_id = data.get("thread_id")
    body = data.get("body", "")
    if not thread_id:
        return jsonify({"error": "Missing thread_id"}), 400
    try:
        draft_id = client.create_draft(thread_id, body)
        return jsonify({"status": "success", "draft_id": draft_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/drafts/<draft_id>", methods=["PUT"])
def update_draft(draft_id: str):
    data = request.json or {}
    body = data.get("body", "")
    try:
        client.update_draft_body(draft_id, body)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/drafts/<draft_id>/attachments", methods=["POST"])
def add_attachment(draft_id: str):
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty file name"}), 400
        
    filename = secure_filename(file.filename)
    
    # Save file to a temp folder first
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, filename)
    file.save(temp_path)
    
    try:
        record = client.add_attachment(draft_id, temp_path)
        return jsonify(record)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.route("/api/drafts/<draft_id>/attachments/<attachment_id>", methods=["DELETE"])
def remove_attachment(draft_id: str, attachment_id: str):
    try:
        client.remove_attachment_from_draft(draft_id, attachment_id)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/drafts/<draft_id>/publish", methods=["POST"])
def publish_draft(draft_id: str):
    try:
        user_id, msg_id, outbox_path = client.publish_draft(draft_id)
        return jsonify({
            "status": "success",
            "source_user_id": user_id,
            "source_local_message_id": msg_id,
            "outbox_package_path": outbox_path
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/receipts", methods=["GET"])
def list_receipts():
    return jsonify(client.list_receipts())

@app.route("/api/pending", methods=["GET"])
def list_pending():
    return jsonify(client.list_pending_outbox())

@app.route("/api/coordinator/scan", methods=["POST"])
def trigger_scan():
    try:
        summary = coordinator.run_scan()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def datetime_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def run_server(port: int = 5000, debug: bool = False):
    global settings, storage, client, coordinator
    settings = load_settings()
    storage = FileSystemStorage()
    client = ThreadMailClient(settings, storage)
    coordinator = CoordinatorService(settings, storage)
    coordinator.register_user(settings.user_id)
    
    if not debug:
        # Start browser automatically in 1 second
        Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.run(host="127.0.0.1", port=port, debug=debug)
