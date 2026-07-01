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
from shikibo.client.client import ThreadMailClient, BAD_VALUE
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
        archive_path = Path(settings.archive_root) / f"{thread_id}.zip"
        if os.path.exists(archive_path):
            import zipfile
            from flask import send_file
            import io
            import mimetypes
            try:
                possible_paths = [
                    f"messages/{msg_folder}/attachments/{filename}",
                    f"{thread_id}/messages/{msg_folder}/attachments/{filename}"
                ]
                with zipfile.ZipFile(str(archive_path)) as zf:
                    found_path = None
                    for p in possible_paths:
                        if p in zf.namelist():
                            found_path = p
                            break
                    if found_path:
                        data = zf.read(found_path)
                        mimetype, _ = mimetypes.guess_type(filename)
                        return send_file(
                            io.BytesIO(data),
                            mimetype=mimetype or "application/octet-stream",
                            as_attachment=True,
                            download_name=filename
                        )
            except Exception as e:
                logger.error(f"Error serving attachment from zip: {e}")
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

@app.route("/api/threads", methods=["GET"])
def list_threads():
    return jsonify(client.list_active_threads())

@app.route("/api/threads/archived", methods=["GET"])
def list_archived_threads():
    return jsonify(client.list_archived_threads())

@app.route("/api/threads/next-id", methods=["GET"])
def get_next_thread_id():
    title = request.args.get("title", "")
    import socket
    import hashlib
    from datetime import datetime, timezone
    hostname = socket.gethostname()
    user = settings.user_id
    role = settings.role
    
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    input_str = f"{hostname}/{user}/{role}/{timestamp}/{title}"
    checksum = hashlib.sha512(input_str.encode("utf-8")).hexdigest()
    proposed_id = f"T_{checksum}"
    
    active_dir = Path(settings.thread_root) / proposed_id
    archive_file = Path(settings.archive_root) / f"{proposed_id}.zip"
    
    while storage.exists(active_dir) or storage.exists(archive_file):
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        input_str = f"{hostname}/{user}/{role}/{timestamp}/{title}"
        checksum = hashlib.sha512(input_str.encode("utf-8")).hexdigest()
        proposed_id = f"T_{checksum}"
        active_dir = Path(settings.thread_root) / proposed_id
        archive_file = Path(settings.archive_root) / f"{proposed_id}.zip"
        
    return jsonify({"thread_id": proposed_id})

@app.route("/api/threads", methods=["POST"])
def create_thread():
    data = request.json or {}
    thread_id = data.get("thread_id")
    title = data.get("title")
    description = data.get("description", "")
    
    if not thread_id or not title:
        return jsonify({"error": "Missing thread_id or title"}), 400
        
    thread_dir = Path(settings.thread_root) / thread_id
    archive_file = Path(settings.archive_root) / f"{thread_id}.zip"
    
    if storage.exists(thread_dir) or storage.exists(archive_file):
        return jsonify({"error": "Thread already exists"}), 400
        
    storage.makedirs(thread_dir)
    storage.makedirs(thread_dir / "messages")
    
    # Save thread.json
    import socket
    from datetime import datetime, timezone
    created_at_gmt = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    hostname = socket.gethostname()
    
    storage.write_file_new(
        thread_dir / "thread.json",
        json.dumps({
            "thread_id": thread_id,
            "title": title,
            "status": "OPEN",
            "created_at": created_at_gmt,
            "hostname": hostname
        }, indent=2)
    )
    
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
    except BAD_VALUE as e:
        return jsonify({"error": str(e)}), 400
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
    except BAD_VALUE as e:
        return jsonify({"error": str(e)}), 400
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
