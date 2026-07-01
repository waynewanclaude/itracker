import os
import re
import json
import sqlite3
import logging
import socket
import getpass
import subprocess

logger = logging.getLogger("shikibo.coordinator")
import zipfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from shikibo.config import Settings
from shikibo.storage import FileSystemStorage

class OutboxWatcherHandler(FileSystemEventHandler):
    def __init__(self, service):
        self.service = service

    def on_created(self, event):
        self._check_and_trigger(event.src_path)

    def on_moved(self, event):
        self._check_and_trigger(event.dest_path)

    def _check_and_trigger(self, file_path_str):
        path = Path(file_path_str)
        if path.name == "message.json":
            parts = path.parts
            if "users" in parts and "outbox" in parts:
                self.service.run_scan()

class CoordinatorService:
    def __init__(self, settings: Settings, storage: FileSystemStorage = None):
        self.settings = settings
        self.storage = storage or FileSystemStorage()
        self.is_service = False

        self.scan_lock = threading.Lock()
        self.observer = None
        
        logger.info("========================================")
        logger.info("CoordinatorService settings:")
        for key, val in self.settings.model_dump().items():
            logger.info(f"  {key}: {val}")
        logger.info("========================================")
        
        self.db_path = Path(self.settings.root_dir) / "system" / "coordinator" / "coordinator_ledger.db"
        self.storage.makedirs(self.db_path.parent)
        self.dead_letter_path = Path(self.settings.root_dir) / "system" / "coordinator" / "dead_letter"
        self.storage.makedirs(self.dead_letter_path)
        
        # Ensure registered_users.txt exists
        self.registered_users_file = Path(self.settings.root_dir) / "system" / "config" / "registered_users.txt"
        self.storage.makedirs(self.registered_users_file.parent)
        if not self.storage.exists(self.registered_users_file):
            self.storage.write_file_new(self.registered_users_file, "")
            
        self._init_db()
        self.rebuild_ledger_if_empty()
        logger.info(f"CoordinatorService initialized. Root directory: {self.settings.root_dir}, Database: {self.db_path}")

    def write_status_file(self, message: str) -> None:
        """Writes/overwrites a timestamped status file `<hostname>-<PID>.txt` in system/coordinator."""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        hostname = socket.gethostname()
        pid = os.getpid()
        status_file = Path(self.settings.root_dir) / "system" / "coordinator" / f"{hostname}-{pid}.txt"
        self.storage.makedirs(status_file.parent)
        if self.storage.exists(status_file):
            self.storage.delete(status_file)
        self.storage.write_file_new(status_file, f"{ts}\n{message}\n")

    def cleanup_old_status_files(self) -> None:
        """Removes any old status files for the current host in system/coordinator."""
        hostname = socket.gethostname()
        pid = os.getpid()
        coord_dir = Path(self.settings.root_dir) / "system" / "coordinator"
        if self.storage.exists(coord_dir) and self.storage.is_dir(coord_dir):
            try:
                entries = self.storage.list_dir(coord_dir)
                for entry in entries:
                    if entry.startswith(f"{hostname}-") and entry.endswith(".txt") and entry != f"{hostname}-{pid}.txt":
                        self.storage.delete(coord_dir / entry)
            except Exception as e:
                logger.warning(f"Failed to clean up old status files: {e}")

    def enforce_service_locks(self) -> None:
        """Enforces host/user validation and process-level active locks.
        Only executed when the coordinator is run as a background service/daemon.
        """
        try:
            self._verify_host_and_user()
            self._check_and_write_pid()
        except SystemExit as e:
            self.write_status_file(f"Exit: {e}")
            raise
        self.cleanup_old_status_files()

    def _verify_host_and_user(self) -> None:
        """Verifies that the coordinator is running on the authorized host and system user."""
        host_file = Path(self.settings.root_dir) / "system" / "config" / "coordinator_host.json"
        if not self.storage.exists(host_file):
            raise SystemExit(
                f"Error: Coordinator host configuration file is missing at:\n  {host_file}\n"
                f"Please create this file with the following format:\n"
                f"  {{\n    \"host\": \"{socket.gethostname()}\",\n    \"user\": \"{getpass.getuser()}\"\n  }}"
            )
            
        try:
            content = self.storage.read_file_text(host_file)
            data = json.loads(content)
        except Exception as e:
            raise SystemExit(f"Error: Failed to read or parse {host_file}: {e}")
            
        config_host = str(data.get("host", "")).strip().lower()
        config_user = str(data.get("user", "")).strip().lower()
        
        current_host = socket.gethostname().strip().lower()
        current_user = getpass.getuser().strip().lower()
        
        if current_host != config_host or current_user != config_user:
            raise SystemExit(
                f"Error: Unauthorized host/user configuration.\n"
                f"Authorized: host='{config_host}', user='{config_user}'\n"
                f"Current:    host='{current_host}', user='{current_user}'"
            )

    def _is_coordinator_process_running(self, pid: int) -> bool:
        """Checks if a process with the given PID is running and is a coordinator process."""
        if os.name == 'nt':
            try:
                cmd = f'wmic process where "ProcessID={pid}" get CommandLine /format:list'
                output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
                if "CommandLine=" in output:
                    cmdline = output.split("CommandLine=", 1)[1].strip()
                    return "shikibo" in cmdline.lower() or "coordinator" in cmdline.lower()
            except Exception:
                pass
            try:
                output = subprocess.check_output(f'tasklist /FI "PID eq {pid}" /NH', shell=True, text=True, stderr=subprocess.DEVNULL)
                return str(pid) in output and ("python" in output.lower() or "shikibo" in output.lower())
            except Exception:
                pass
        else:
            # POSIX (Linux/macOS)
            try:
                os.kill(pid, 0)
            except OSError:
                return False
                
            try:
                cmdline_path = Path(f"/proc/{pid}/cmdline")
                if cmdline_path.exists():
                    cmdline = cmdline_path.read_text(encoding="utf-8", errors="ignore").replace("\x00", " ")
                    return "shikibo" in cmdline.lower() or "coordinator" in cmdline.lower()
            except Exception:
                pass
                
            try:
                output = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True, stderr=subprocess.DEVNULL)
                return "shikibo" in output.lower() or "coordinator" in output.lower()
            except Exception:
                pass
                
            return True
        return False

    def _check_and_write_pid(self) -> None:
        """Verifies if a coordinator is already running via PID check, otherwise writes current PID."""
        pid_file = Path(self.settings.root_dir) / "system" / "coordinator" / "coordinator_pid.txt"
        if self.storage.exists(pid_file):
            try:
                content = self.storage.read_file_text(pid_file).strip()
                existing_pid = int(content)
                if existing_pid == os.getpid():
                    # Same process, ignore
                    return
                if self._is_coordinator_process_running(existing_pid):
                    raise SystemExit(
                        f"Error: Another coordinator process (PID {existing_pid}) is already running on this machine."
                    )
            except (ValueError, TypeError):
                # Invalid PID content in file, ignore and proceed
                pass
            except SystemExit:
                raise
            except Exception as e:
                logger.warning(f"Error checking coordinator_pid.txt: {e}. Claiming throne anyway.")
                
        own_pid = os.getpid()
        try:
            self.storage.makedirs(pid_file.parent)
            if self.storage.exists(pid_file):
                self.storage.delete(pid_file)
            self.storage.write_file_new(pid_file, str(own_pid))
            logger.info(f"Coordinator claimed the throne. Written PID {own_pid} to {pid_file}")
        except Exception as e:
            raise SystemExit(f"Error: Failed to write coordinator PID to {pid_file}: {e}")

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
            
        logger.info("Coordinator SQLite database empty. Rebuilding ledger state from filesystem threads...")
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
                    safe_user_id = parts[2]
                    user_id = safe_user_id.replace(".", "/", 1)
                    local_id = parts[3]
                    
                    cursor.execute("""
                        INSERT OR IGNORE INTO ledger 
                        (source_user_id, source_local_message_id, target_thread_id, distributed_filename, distributed_counter, distributed_at, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (user_id, local_id, thread_id, folder_name, counter, dist_time, "distributed"))
                    
        conn.commit()
        conn.close()
        logger.info("Rebuild complete.")

    def _is_temp_name(self, name: str) -> bool:
        """Checks if a file/directory name is a temporary sync artifact."""
        return name.startswith(".") or name.startswith("~") or name.endswith(".tmp")

    def start_fs_watcher(self) -> None:
        """Starts a background filesystem watchdog to monitor registered outboxes recursively."""
        users_dir = Path(self.settings.root_dir) / "users"
        self.storage.makedirs(users_dir)
        
        handler = OutboxWatcherHandler(self)
        self.observer = Observer()
        self.observer.schedule(handler, path=str(users_dir), recursive=True)
        self.observer.start()
        logger.info(f"[Watcher] Started outbox filesystem watcher on {users_dir}")

    def stop_fs_watcher(self) -> None:
        """Stops the outbox filesystem watchdog."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("[Watcher] Stopped outbox filesystem watcher.")

    def get_registered_users(self) -> List[str]:
        """Reads registered user/folder names from config/registered_users.txt."""
        content = self.storage.read_file_text(self.registered_users_file)
        users = []
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                users.append(line)
        return users

    def get_registered_outboxes(self) -> List[str]:
        """Dynamically constructs outbox paths for all registered users and their roles using settings.root_dir."""
        users = self.get_registered_users()
        outboxes = []
        for user in users:
            user_dir = Path(self.settings.root_dir) / "users" / user
            # 1. Top-level outbox (if it exists)
            outbox_path = user_dir / "outbox"
            if self.storage.exists(outbox_path):
                outboxes.append(os.path.normpath(str(outbox_path)))
            
            # 2. Role outboxes
            if self.storage.exists(user_dir) and self.storage.is_dir(user_dir):
                try:
                    entries = self.storage.list_dir(user_dir)
                    for entry in entries:
                        if entry in ("outbox", "receipts", ".processed") or self._is_temp_name(entry):
                            continue
                        if not re.match(r"^[a-zA-Z0-9_]+$", entry):
                            logger.warning(f"Discarding invalid role directory name: {entry}")
                            continue
                        role_dir = user_dir / entry
                        role_outbox = role_dir / "outbox"
                        if self.storage.is_dir(role_dir) and self.storage.exists(role_outbox):
                            outboxes.append(os.path.normpath(str(role_outbox)))
                except Exception:
                    pass
        return outboxes

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
        users_root = Path(self.settings.root_dir) / "users"
        try:
            rel_parts = pkg_path.relative_to(users_root).parts
            if len(rel_parts) == 3 and rel_parts[1] == "outbox":
                expected_user_id = rel_parts[0]
            elif len(rel_parts) == 4 and rel_parts[2] == "outbox":
                expected_user_id = f"{rel_parts[0]}/{rel_parts[1]}"
            else:
                expected_user_id = ""
        except ValueError:
            expected_user_id = ""

        if user_id != expected_user_id:
            return {
                "status": "malformed", 
                "reason": f"Security mismatch: source_user_id '{user_id}' does not match outbox owner directory '{expected_user_id}'"
            }

        # Verify that the top-level user is registered
        top_level_user = user_id.split("/")[0]
        if top_level_user not in self.get_registered_users():
            return {
                "status": "malformed",
                "reason": f"Security mismatch: top-level user '{top_level_user}' is not registered"
            }
            
        # Deduplication check
        if self.is_message_distributed(user_id, local_id):
            # Check if receipt exists, write it if missing
            self.write_receipt_if_missing(user_id, local_id, thread_id)
            return {"status": "duplicate", "user_id": user_id, "local_id": local_id}

        # Force directory listing refresh to clear virtual drive caching (e.g. Google Drive) on Windows
        try:
            self.storage.list_dir(Path(self.settings.thread_root))
            thread_dir = Path(self.settings.thread_root) / thread_id
            if self.storage.exists(thread_dir):
                self.storage.list_dir(thread_dir)
        except Exception:
            pass

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
        
        safe_user_id = user_id.replace("/", ".")
        dist_folder_name = f"{dist_timestamp}_{counter:06d}_{safe_user_id}_{local_id}"
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
        if "/" in user_id:
            u_id, role = user_id.split("/", 1)
            user_receipts_dir = Path(self.settings.root_dir) / "users" / u_id / role / "receipts"
        else:
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
            receipt_path = user_receipts_dir / receipt_filename
            if self.storage.exists(receipt_path):
                self.storage.delete(receipt_path)
            self.storage.write_file_new(
                receipt_path,
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
        if "/" in user_id:
            u_id, role = user_id.split("/", 1)
            user_receipts_dir = Path(self.settings.root_dir) / "users" / u_id / role / "receipts"
        else:
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
        if getattr(self, "is_service", False):
            self._verify_host_and_user()

        with self.scan_lock:
            registered = self.get_registered_outboxes()
            logger.info(f"Scanning outboxes: {registered}")
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
                    if entry == ".processed" or self._is_temp_name(entry):
                        continue
                        
                    pkg_path = path / entry
                    if self.storage.is_dir(pkg_path):
                        res = self.process_outbox_package(pkg_path)
                        
                        status = res.get("status")
                        if status == "success":
                            summary["processed"] += 1
                            logger.info(f"Successfully processed outbox package '{entry}' from user '{res.get('user_id')}' distributed as '{res.get('folder_name')}'")
                            # Move to outbox's internal .processed subfolder to clean up outbox queue
                            processed_dir = path / ".processed"
                            self.storage.makedirs(processed_dir)
                            self.storage.rename_or_finalize(pkg_path, processed_dir / entry)
                        elif status == "duplicate":
                            summary["duplicates"] += 1
                            logger.info(f"Detected duplicate outbox package '{entry}' for user '{res.get('user_id')}', deleting from outbox queue")
                            self.storage.delete(pkg_path)
                        elif status in ["malformed", "invalid_thread", "closed_thread"]:
                            summary["dead_lettered"] += 1
                            logger.warning(f"Quarantining invalid package '{entry}' to dead-letter folder. Reason: {res.get('reason')}")
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
                                logger.error(f"Failed to move package '{entry}' to dead-letter folder: {e}")
                                summary["errors"].append(f"Failed to move package {entry} to dead_letter: {e}")
                        elif status == "unstable":
                            # Skip this package and process it on the next run
                            logger.info(f"Package '{entry}' is currently unstable, skipping for next scan")
                            pass
                            
            if summary["processed"] > 0 or summary["duplicates"] > 0 or summary["dead_lettered"] > 0:
                logger.info(f"Scan complete: {summary}")
                
            if getattr(self, "is_service", False) and summary["processed"] > 0:
                self.write_status_file(f"Dispatched {summary['processed']} messages in the loop.")
                
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
            logger.error(f"Failed to archive thread {thread_id}: {e}")
            if self.storage.exists(archive_file):
                self.storage.delete(archive_file)
                
        return False
