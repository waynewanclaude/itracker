import os
import json
import tempfile
import webbrowser
from threading import Timer
from flask import Flask, request, jsonify, send_from_directory, Response
from werkzeug.utils import secure_filename
from pathlib import Path
from queue import Queue
import logging

from shikibo.config import load_settings
from shikibo.storage import FileSystemStorage
from shikibo.client.client import ThreadMailClient
from shikibo.coordinator.service import CoordinatorService

logger = logging.getLogger("shikibo.webapp")

app = Flask(__name__, static_folder="static", static_url_path="")

# Placeholders for global settings and components (reassigned upon run_server execution)
settings = None
storage = None
client = None
coordinator = None
observer = None

sse_clients = []

def notify_clients(event_name="refresh"):
    for q in list(sse_clients):
        q.put(event_name)

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
        "role": settings.role,
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

@app.route("/api/threads/next-id", methods=["GET"])
def get_next_thread_id():
    from datetime import datetime, timezone
    import re
    try:
        storage.list_dir(Path(settings.thread_root))
    except Exception:
        pass
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"T_{today_str}_"
    pattern = re.compile(rf"^T_{today_str}_([0-9]+)$")
    existing_seqs = []
    if storage.exists(settings.thread_root):
        for item in storage.list_dir(settings.thread_root):
            match = pattern.match(item)
            if match:
                try:
                    existing_seqs.append(int(match.group(1)))
                except ValueError:
                    pass
    next_seq = 1
    if existing_seqs:
        next_seq = max(existing_seqs) + 1
    while True:
        proposed_id = f"{prefix}{next_seq:04d}"
        proposed_dir = Path(settings.thread_root) / proposed_id
        if not storage.exists(proposed_dir):
            return jsonify({"thread_id": proposed_id})
        next_seq += 1

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

@app.route("/api/events")
def stream_events():
    def event_generator():
        q = Queue()
        sse_clients.append(q)
        try:
            yield "data: connected\n\n"
            while True:
                event_data = q.get()
                yield f"data: {event_data}\n\n"
        except GeneratorExit:
            if q in sse_clients:
                sse_clients.remove(q)
    return Response(event_generator(), mimetype="text/event-stream")

def datetime_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def run_server(settings_obj, port: int = 5000, debug: bool = False):
    import socket
    global settings, storage, client, coordinator, observer
    settings = settings_obj
    
    from shikibo.config import setup_logging
    setup_logging(settings)
    
    # Dump finalized parameters right before initializing the WebApp components
    logger.info("========================================")
    logger.info("INITIALIZING SHIKIBO WEBAPP SYSTEM:")
    for key, val in settings.model_dump().items():
        logger.info(f"  {key}: {val}")
    logger.info("========================================")
    
    storage = FileSystemStorage()
    client = ThreadMailClient(settings, storage)
    coordinator = CoordinatorService(settings, storage)
    coordinator.register_user(settings.user_id)
    
    # Initialize filesystem watcher for SSE client refresh if enabled
    if settings.use_fs_events:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        
        class ThreadsWatcherHandler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                name = Path(event.src_path).name
                if name.startswith(".") or name.startswith("~") or name.endswith(".tmp"):
                    return
                notify_clients("refresh")
                
        threads_dir = Path(settings.thread_root)
        storage.makedirs(threads_dir)
        
        handler = ThreadsWatcherHandler()
        observer = Observer()
        observer.schedule(handler, path=str(threads_dir), recursive=True)
        observer.start()
        logger.info(f"[Watcher] WebApp streaming events enabled, watching {threads_dir}")
        
    # Automatically find an available port if the specified port is occupied
    actual_port = port
    while actual_port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", actual_port))
                break
            except OSError:
                actual_port += 1
                
    if actual_port != port:
        logger.warning(f"Port {port} is occupied. Automatically bound to available port {actual_port}.")
        
    if not debug:
        # Start browser automatically in 1 second on the actual port
        Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{actual_port}/")).start()
    app.run(host="127.0.0.1", port=actual_port, debug=debug)
