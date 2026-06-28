import os
import re
import json
import sqlite3
import zipfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

from src.config import Settings
from src.storage import FileSystemStorage

class CoordinatorService:
    def __init__(self, settings: Settings, storage: FileSystemStorage = None):
        self.settings = settings
        self.storage = storage or FileSystemStorage()
        
        self.db_path = Path(self.settings.root_dir) / "coordinator" / "coordinator_ledger.db"
        self.storage.makedirs(self.db_path.parent)
        self.dead_letter_path = Path(self.settings.root_dir) / "coordinator" / "dead_letter"
        self.storage.makedirs(self.dead_letter_path)
        
        # Ensure registered_outboxes.txt exists
        self.registered_outboxes_file = Path(self.settings.root_dir) / "config" / "registered_outboxes.txt"
        self.storage.makedirs(self.registered_outboxes_file.parent)
        if not self.storage.exists(self.registered_outboxes_file):
            self.storage.write_file_new(self.registered_outboxes_file, "")
            
        self._init_db()
        self.rebuild_ledger_if_empty()

    def _init_db(self) -> None:
        """Initializes SQLite ledger for distributed message deduplication."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                source_user_id TEXT,
                source_local_message_id TEXT,
                target_thread_id TEXT,
                distributed_filename TEXT,
                distributed_counter INTEGER,
                distributed_at TEXT,
                status TEXT,
                PRIMARY KEY (source_user_id, source_local_message_id)
            )
        """)
        conn.commit()
        conn.close()

    def rebuild_ledger_if_empty(self) -> None:
        """Self-healing: Rebuilds SQLite state by scanning existing distributed threads on disk."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ledger")
        count = cursor.fetchone()[0]
        if count > 0:
            conn.close()
            return
            
        print("Coordinator SQLite database empty. Rebuilding ledger state from filesystem threads...")
        thread_root = Path(self.settings.thread_root)
        if not self.storage.exists(thread_root):
            conn.close()
            return
            
        threads = self.storage.list_dir(thread_root)
        for thread_id in threads:
            messages_dir = thread_root / thread_id / "messages"
            if not self.storage.exists(messages_dir):
                continue
                
            msg_folders = self.storage.list_dir(messages_dir)
            for folder_name in msg_folders:
                # Folder name format: 20260627T161501Z_000001_user_U000001
                # Parse details
                parts = folder_name.split("_")
                if len(parts) >= 4:
                    dist_time = parts[0]
                    try:
                        counter = int(parts[1])
                    except ValueError:
                        counter = 0
                    user_id = parts[2]
                    local_id = parts[3]
                    
                    cursor.execute("""
                        INSERT OR IGNORE INTO ledger 
                        (source_user_id, source_local_message_id, target_thread_id, distributed_filename, distributed_counter, distributed_at, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (user_id, local_id, thread_id, folder_name, counter, dist_time, "distributed"))
                    
        conn.commit()
        conn.close()
        print("Rebuild complete.")

    def get_registered_outboxes(self) -> List[str]:
        """Reads outbox paths from config/registered_outboxes.txt."""
        content = self.storage.read_file_text(self.registered_outboxes_file)
        outboxes = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                outboxes.append(line)
        return outboxes

    def register_outbox(self, path: str) -> None:
        """Helper to register a new outbox path in registered_outboxes.txt."""
        outboxes = self.get_registered_outboxes()
        if path not in outboxes:
            outboxes.append(path)
            # Write back
            content = "\n".join(outboxes) + "\n"
            self.storage.delete(self.registered_outboxes_file)
            self.storage.write_file_new(self.registered_outboxes_file, content)

    def is_message_distributed(self, user_id: str, local_id: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM ledger WHERE source_user_id = ? AND source_local_message_id = ?
        """, (user_id, local_id))
        res = cursor.fetchone()
        conn.close()
        return res is not None

    def _get_next_thread_counter(self, thread_id: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MAX(distributed_counter) FROM ledger WHERE target_thread_id = ?
        """, (thread_id,))
        res = cursor.fetchone()[0]
        conn.close()
        return (res or 0) + 1

    def _record_distribution(self, user_id: str, local_id: str, thread_id: str, filename: str, counter: int, timestamp: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO ledger 
            (source_user_id, source_local_message_id, target_thread_id, distributed_filename, distributed_counter, distributed_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, local_id, thread_id, filename, counter, timestamp, "distributed"))
        conn.commit()
        conn.close()

    def process_outbox_package(self, pkg_path: Path) -> Dict[str, Any]:
        """Processes a single outbox message package and distributes it."""
        meta_path = pkg_path / "message.json"
        body_path = pkg_path / "body.md"
        
        # Stability check
        if not self.storage.exists(meta_path) or not self.storage.exists(body_path):
            return {"status": "unstable", "reason": "Missing message.json or body.md"}
            
        try:
            meta = json.loads(self.storage.read_file_text(meta_path))
        except Exception as e:
            return {"status": "malformed", "reason": f"Invalid message.json JSON formatting: {e}"}
            
        # Validate critical fields
        schema = meta.get("schema_version")
        user_id = meta.get("source_user_id")
        local_id = meta.get("source_local_message_id")
        thread_id = meta.get("target_thread_id")
        
        if not all([schema, user_id, local_id, thread_id]):
            return {"status": "malformed", "reason": "Missing required metadata fields"}
            
        # Verify that the declared source_user_id matches the owner of the outbox directory
        expected_user_id = pkg_path.parent.parent.name
        if user_id != expected_user_id:
            return {
                "status": "malformed", 
                "reason": f"Security mismatch: source_user_id '{user_id}' does not match outbox owner directory '{expected_user_id}'"
            }
            
        # Deduplication check
        if self.is_message_distributed(user_id, local_id):
            # Check if receipt exists, write it if missing
            self.write_receipt_if_missing(user_id, local_id, thread_id)
            return {"status": "duplicate", "user_id": user_id, "local_id": local_id}

        # Check if thread is valid and open
        thread_dir = Path(self.settings.thread_root) / thread_id
        thread_meta_path = thread_dir / "thread.json"
        if not self.storage.exists(thread_meta_path):
            return {"status": "invalid_thread", "reason": f"Thread {thread_id} does not exist"}
            
        try:
            thread_meta = json.loads(self.storage.read_file_text(thread_meta_path))
            if thread_meta.get("status") == "DONE":
                return {"status": "closed_thread", "reason": f"Thread {thread_id} is marked DONE and closed"}
        except Exception as e:
            return {"status": "malformed_thread", "reason": f"Failed to parse thread.json: {e}"}

        # Safe Distribution steps
        now_utc = datetime.now(timezone.utc)
        dist_timestamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
        counter = self._get_next_thread_counter(thread_id)
        
        dist_folder_name = f"{dist_timestamp}_{counter:06d}_{user_id}_{local_id}"
        target_message_dir = thread_dir / "messages" / dist_folder_name
        
        # 1. Copy to thread
        try:
            self.storage.makedirs(target_message_dir.parent)
            self.storage.copy_tree(pkg_path, target_message_dir)
        except Exception as e:
            return {"status": "failed", "reason": f"Failed during copy to thread: {e}"}
            
        # 2. Verify copy
        if not self.storage.exists(target_message_dir / "message.json"):
            return {"status": "failed", "reason": "Verification failed: message.json not found in thread"}

        # 3. Write receipt to source user receipts folder
        receipt_filename = f"{local_id}_receipt.json"
        user_receipts_dir = Path(self.settings.root_dir) / "users" / user_id / "receipts"
        self.storage.makedirs(user_receipts_dir)
        
        receipt_data = {
            "source_user_id": user_id,
            "source_local_message_id": local_id,
            "target_thread_id": thread_id,
            "distributed_filename": dist_folder_name,
            "distributed_at": now_utc.isoformat(),
            "distributed_counter": counter,
            "status": "distributed"
        }
        
        try:
            self.storage.write_file_new(
                user_receipts_dir / receipt_filename,
                json.dumps(receipt_data, indent=2)
            )
        except Exception as e:
            # Clean up target folder and fail to maintain atomicity
            self.storage.delete(target_message_dir)
            return {"status": "failed", "reason": f"Failed to write receipt: {e}"}

        # 4. Record ledger
        self._record_distribution(user_id, local_id, thread_id, dist_folder_name, counter, dist_timestamp)
        
        return {"status": "success", "user_id": user_id, "local_id": local_id, "folder_name": dist_folder_name}

    def write_receipt_if_missing(self, user_id: str, local_id: str, thread_id: str) -> None:
        """Rewrites a receipt if missing for a previously distributed message."""
        receipt_filename = f"{local_id}_receipt.json"
        user_receipts_dir = Path(self.settings.root_dir) / "users" / user_id / "receipts"
        receipt_file = user_receipts_dir / receipt_filename
        
        if not self.storage.exists(receipt_file):
            # Fetch ledger details
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT distributed_filename, distributed_counter, distributed_at 
                FROM ledger WHERE source_user_id = ? AND source_local_message_id = ?
            """, (user_id, local_id))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                filename, counter, timestamp = row
                receipt_data = {
                    "source_user_id": user_id,
                    "source_local_message_id": local_id,
                    "target_thread_id": thread_id,
                    "distributed_filename": filename,
                    "distributed_at": timestamp,
                    "distributed_counter": counter,
                    "status": "distributed"
                }
                self.storage.makedirs(user_receipts_dir)
                try:
                    self.storage.write_file_new(receipt_file, json.dumps(receipt_data, indent=2))
                except Exception:
                    pass

    def run_scan(self) -> Dict[str, Any]:
        """Scans all registered outboxes and processes pending messages.
        Moves successfully distributed packages to an outbox .processed subfolder.
        """
        registered = self.get_registered_outboxes()
        summary = {
            "scanned_outboxes": len(registered),
            "processed": 0,
            "duplicates": 0,
            "dead_lettered": 0,
            "errors": []
        }
        
        for outbox_path in registered:
            path = Path(outbox_path)
            if not self.storage.exists(path):
                continue
                
            entries = self.storage.list_dir(path)
            for entry in entries:
                if entry == ".processed":
                    continue
                    
                pkg_path = path / entry
                if self.storage.is_dir(pkg_path):
                    res = self.process_outbox_package(pkg_path)
                    
                    status = res.get("status")
                    if status == "success":
                        summary["processed"] += 1
                        # Move to outbox's internal .processed subfolder to clean up outbox queue
                        processed_dir = path / ".processed"
                        self.storage.makedirs(processed_dir)
                        self.storage.rename_or_finalize(pkg_path, processed_dir / entry)
                    elif status == "duplicate":
                        summary["duplicates"] += 1
                        # Safe cleanup: since it's already distributed, delete the outbox duplicate
                        self.storage.delete(pkg_path)
                    elif status in ["malformed", "invalid_thread", "closed_thread"]:
                        summary["dead_lettered"] += 1
                        summary["errors"].append(f"Package {entry}: {res.get('reason')}")
                        # Move to coordinator dead-letter folder
                        dl_dest = self.dead_letter_path / entry
                        # If dead-letter target already exists, append unique suffix
                        if self.storage.exists(dl_dest):
                            dl_dest = self.dead_letter_path / f"{entry}_{int(datetime.now().timestamp())}"
                        try:
                            # Save error explanation file inside the dead letter folder
                            self.storage.rename_or_finalize(pkg_path, dl_dest)
                            self.storage.write_file_new(dl_dest / "dead_letter_reason.txt", res.get("reason"))
                        except Exception as e:
                            summary["errors"].append(f"Failed to move package {entry} to dead_letter: {e}")
                    elif status == "unstable":
                        # Skip this package and process it on the next run
                        pass
                        
        return summary

    def archive_thread(self, thread_id: str) -> bool:
        """Archives a open/done thread into a zip package, cleaning up the live folder."""
        thread_dir = Path(self.settings.thread_root) / thread_id
        if not self.storage.exists(thread_dir):
            return False
            
        archive_file = Path(self.settings.archive_root) / f"T_{thread_id}.zip"
        if self.storage.exists(archive_file):
            return False
            
        # Create ZIP archive
        self.storage.makedirs(archive_file.parent)
        try:
            with zipfile.ZipFile(archive_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add all files recursively
                for root, dirs, files in os.walk(thread_dir):
                    for file in files:
                        full_path = Path(root) / file
                        rel_path = full_path.relative_to(thread_dir.parent)
                        zipf.write(full_path, rel_path)
                        
            # Verify and delete live thread directory
            if self.storage.exists(archive_file):
                self.storage.delete(thread_dir)
                return True
        except Exception as e:
            print(f"Failed to archive thread {thread_id}: {e}")
            if self.storage.exists(archive_file):
                self.storage.delete(archive_file)
                
        return False
