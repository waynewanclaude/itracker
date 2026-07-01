import os
import re
import json
import uuid
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import mimetypes
import logging

from shikibo.config import Settings, BAD_VALUE
from shikibo.storage import FileSystemStorage

logger = logging.getLogger("shikibo.client")

class ThreadMailClient:
    def __init__(self, settings: Settings, storage: FileSystemStorage = None):
        self.settings = settings
        self.storage = storage or FileSystemStorage()
        
        logger.info("========================================")
        logger.info("ThreadMailClient settings:")
        for key, val in self.settings.model_dump().items():
            logger.info(f"  {key}: {val}")
        logger.info("========================================")
        
        # Ensure workspace directories exist
        self.storage.makedirs(self.settings.local_draft_root)
        self.storage.makedirs(self.settings.outbox_root)
        self.storage.makedirs(self.settings.receipt_root)
        self.storage.makedirs(self.settings.thread_root)
        self.storage.makedirs(self.settings.index_root)
        self.storage.makedirs(self.settings.archive_root)

    # --- Draft Management API ---
    
    def _get_draft_path(self, draft_id: str) -> Path:
        return Path(self.settings.local_draft_root) / f"draft_{draft_id}"

    def create_draft(self, thread_id: str, body: str = "", attachments: List[str] = None) -> str:
        """Creates a local, mutable draft and returns the draft_id."""
        archive_file = Path(self.settings.archive_root) / f"{thread_id}.zip"
        thread_dir = Path(self.settings.thread_root) / thread_id
        thread_meta_file = thread_dir / "thread.json"
        
        if not self.storage.exists(thread_meta_file):
            if self.storage.exists(archive_file):
                raise BAD_VALUE(f"Target thread {thread_id} is archived and closed.")
            raise ValueError(f"Target thread {thread_id} does not exist in the shared workspace.")
            
        try:
            thread_meta = json.loads(self.storage.read_file_text(thread_meta_file))
            status = thread_meta.get("status")
            if status == "LOCK":
                raise BAD_VALUE(f"Target thread {thread_id} is locked.")
            elif status == "RESTRICT":
                creator = thread_meta.get("creator_user_id")
                current_user_id = f"{self.settings.user_id}/{self.settings.role}" if self.settings.role else self.settings.user_id
                def is_same_user(id1, id2):
                    if not id1 or not id2:
                        return False
                    norm1 = id1[:-8] if id1.endswith("/__DEF__") else id1
                    norm2 = id2[:-8] if id2.endswith("/__DEF__") else id2
                    return norm1 == norm2
                if creator and not is_same_user(creator, current_user_id):
                    raise BAD_VALUE("Target thread is restricted to its creator only.")
        except BAD_VALUE:
            raise
        except Exception as e:
            raise ValueError(f"Failed to verify thread status: {e}")

        draft_id = str(uuid.uuid4())[:8]
        draft_path = self._get_draft_path(draft_id)
        self.storage.makedirs(draft_path)
        self.storage.makedirs(draft_path / "attachments")
        
        # Save draft.json metadata
        draft_meta = {
            "draft_id": draft_id,
            "thread_id": thread_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "attachments": []
        }
        
        self.storage.write_file_new(
            draft_path / "draft.json", 
            json.dumps(draft_meta, indent=2)
        )
        self.storage.write_file_new(
            draft_path / "body.md", 
            body
        )
        
        if attachments:
            for attach_path in attachments:
                self.add_attachment(draft_id, attach_path)
                
        logger.info(f"Created local draft '{draft_id}' for thread '{thread_id}'")
        return draft_id

    def read_draft(self, draft_id: str) -> Dict[str, Any]:
        """Reads draft metadata and body content."""
        draft_path = self._get_draft_path(draft_id)
        if not self.storage.exists(draft_path):
            raise FileNotFoundError(f"Draft {draft_id} not found.")
            
        meta = json.loads(self.storage.read_file_text(draft_path / "draft.json"))
        body = self.storage.read_file_text(draft_path / "body.md")
        meta["body"] = body
        return meta

    def update_draft_body(self, draft_id: str, body: str) -> None:
        """Updates the text body of a draft."""
        draft_path = self._get_draft_path(draft_id)
        if not self.storage.exists(draft_path):
            raise FileNotFoundError(f"Draft {draft_id} not found.")
            
        # Overwrite body.md
        body_path = draft_path / "body.md"
        self.storage.delete(body_path)
        self.storage.write_file_new(body_path, body)

    def add_attachment(self, draft_id: str, filepath: str, media_type: str = None) -> Dict[str, Any]:
        """Copies an attachment file into the draft attachments area."""
        draft_path = self._get_draft_path(draft_id)
        if not self.storage.exists(draft_path):
            raise FileNotFoundError(f"Draft {draft_id} not found.")
            
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Source attachment file not found: {filepath}")
            
        # Load metadata
        meta_path = draft_path / "draft.json"
        meta = json.loads(self.storage.read_file_text(meta_path))
        
        # Generate attachment ID (A000001, A000002, etc.)
        existing_count = len(meta["attachments"])
        attach_id = f"A{existing_count + 1:06d}"
        
        orig_filename = os.path.basename(filepath)
        stored_filename = f"{attach_id}_{orig_filename}"
        dest_path = draft_path / "attachments" / stored_filename
        
        # Copy to draft attachments directory
        shutil.copy2(filepath, dest_path)
        
        if not media_type:
            media_type, _ = mimetypes.guess_type(filepath)
            if not media_type:
                media_type = "application/octet-stream"
                
        attach_record = {
            "attachment_id": attach_id,
            "original_filename": orig_filename,
            "stored_filename": stored_filename,
            "media_type": media_type,
            "relative_path": f"attachments/{stored_filename}"
        }
        
        meta["attachments"].append(attach_record)
        
        # Overwrite draft.json
        self.storage.delete(meta_path)
        self.storage.write_file_new(meta_path, json.dumps(meta, indent=2))
        return attach_record

    def remove_attachment_from_draft(self, draft_id: str, attachment_id: str) -> None:
        """Removes attachment file and record from the draft."""
        draft_path = self._get_draft_path(draft_id)
        if not self.storage.exists(draft_path):
            raise FileNotFoundError(f"Draft {draft_id} not found.")
            
        meta_path = draft_path / "draft.json"
        meta = json.loads(self.storage.read_file_text(meta_path))
        
        updated_attachments = []
        target_record = None
        for record in meta["attachments"]:
            if record["attachment_id"] == attachment_id:
                target_record = record
            else:
                updated_attachments.append(record)
                
        if not target_record:
            raise KeyError(f"Attachment {attachment_id} not found in draft.")
            
        # Delete file
        file_path = draft_path / target_record["relative_path"]
        self.storage.delete(file_path)
        
        # Save updated metadata
        meta["attachments"] = updated_attachments
        self.storage.delete(meta_path)
        self.storage.write_file_new(meta_path, json.dumps(meta, indent=2))

    def list_drafts(self) -> List[Dict[str, Any]]:
        """Lists all local drafts."""
        drafts = []
        entries = self.storage.list_dir(self.settings.local_draft_root)
        for name in entries:
            if name.startswith("draft_"):
                draft_id = name.split("_")[1]
                try:
                    drafts.append(self.read_draft(draft_id))
                except Exception:
                    pass
        return drafts

    def delete_local_draft(self, draft_id: str) -> None:
        """Permanently deletes a draft and its files."""
        draft_path = self._get_draft_path(draft_id)
        self.storage.delete(draft_path)

    # --- Outbox / Publishing API ---
    
    def _generate_next_local_message_id(self) -> str:
        """Analyzes active outbox folder, processed outbox folder, and receipts to generate the next ID: U000001, U000002..."""
        max_id = 0
        pattern = re.compile(r"^U(\d{6})(_|$)")
        
        # Scan outbox root
        if self.storage.exists(self.settings.outbox_root):
            entries = self.storage.list_dir(self.settings.outbox_root)
            for entry in entries:
                match = pattern.match(entry)
                if match:
                    max_id = max(max_id, int(match.group(1)))
                    
        # Scan outbox .processed subfolder
        processed_path = Path(self.settings.outbox_root) / ".processed"
        if self.storage.exists(processed_path):
            entries = self.storage.list_dir(processed_path)
            for entry in entries:
                match = pattern.match(entry)
                if match:
                    max_id = max(max_id, int(match.group(1)))
                    
        # Scan receipts
        receipt_pattern = re.compile(r"^U(\d{6})_receipt\.json$")
        if self.storage.exists(self.settings.receipt_root):
            entries = self.storage.list_dir(self.settings.receipt_root)
            for entry in entries:
                match = receipt_pattern.match(entry)
                if match:
                    max_id = max(max_id, int(match.group(1)))
                    
        return f"U{max_id + 1:06d}"

    def publish_draft(self, draft_id: str) -> Tuple[str, str, str]:
        """Publishes local draft into the user outbox.
        Returns: Tuple of (source_user_id, source_local_message_id, outbox_package_path)
        """
        draft_data = self.read_draft(draft_id)
        draft_path = self._get_draft_path(draft_id)
        
        # Verify message body is not empty
        body_text = draft_data.get("body", "")
        if not body_text or not body_text.strip():
            raise BAD_VALUE("Message body cannot be empty.")
        
        # Verify target thread exists and is not closed/archived
        thread_id = draft_data.get("thread_id")
        archive_file = Path(self.settings.archive_root) / f"{thread_id}.zip"
        thread_dir = Path(self.settings.thread_root) / thread_id
        thread_meta_file = thread_dir / "thread.json"
        
        if not self.storage.exists(thread_meta_file):
            if self.storage.exists(archive_file):
                raise BAD_VALUE(f"Target thread {thread_id} is archived and closed.")
            raise ValueError(f"Target thread {thread_id} does not exist in the shared workspace.")
            
        try:
            thread_meta = json.loads(self.storage.read_file_text(thread_meta_file))
            status = thread_meta.get("status")
            if status == "LOCK":
                raise BAD_VALUE(f"Target thread {thread_id} is locked.")
            elif status == "RESTRICT":
                creator = thread_meta.get("creator_user_id")
                current_user_id = f"{self.settings.user_id}/{self.settings.role}" if self.settings.role else self.settings.user_id
                def is_same_user(id1, id2):
                    if not id1 or not id2:
                        return False
                    norm1 = id1[:-8] if id1.endswith("/__DEF__") else id1
                    norm2 = id2[:-8] if id2.endswith("/__DEF__") else id2
                    return norm1 == norm2
                if creator and not is_same_user(creator, current_user_id):
                    raise BAD_VALUE("Target thread is restricted to its creator only.")
        except BAD_VALUE:
            raise
        except Exception as e:
            raise ValueError(f"Failed to verify thread status: {e}")
            
        # 1. Assign next local message ID
        msg_id = self._generate_next_local_message_id()
        
        # 2. Build staging folder name
        unique_suffix = str(uuid.uuid4())[:8]
        pkg_name = f"{msg_id}_{unique_suffix}"
        
        # Staging folder should be written locally inside a temporary directory
        # first, then moved to the outbox to ensure atomicity.
        staging_root = Path(self.settings.local_draft_root) / "staging"
        self.storage.makedirs(staging_root)
        staging_pkg_path = staging_root / pkg_name
        self.storage.makedirs(staging_pkg_path)
        
        # Compute body hash for content validation
        body_text = draft_data["body"]
        body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        
        user_id_val = f"{self.settings.user_id}/{self.settings.role}" if self.settings.role else self.settings.user_id
        
        # Extract mentions automatically from body_text (e.g. @username or @username/role)
        # Matches alphanumeric characters, slashes, dashes, and underscores after @
        mentions = re.findall(r"@([\w/-]+)", body_text)
        
        # 3. Create message.json
        message_meta = {
            "schema_version": "1.0",
            "source_user_id": user_id_val,
            "source_local_message_id": msg_id,
            "target_thread_id": draft_data["thread_id"],
            "message_type": "text/markdown",
            "body_file": "body.md",
            "attachments": draft_data["attachments"],
            "mentions": mentions,
            "local_created_at": datetime.now(timezone.utc).isoformat(),
            "content_hash": body_hash
        }
        
        self.storage.write_file_new(
            staging_pkg_path / "message.json", 
            json.dumps(message_meta, indent=2)
        )
        
        # 4. Write body.md
        self.storage.write_file_new(
            staging_pkg_path / "body.md", 
            body_text
        )
        
        # 5. Copy attachments from draft to staging
        if draft_data["attachments"]:
            self.storage.makedirs(staging_pkg_path / "attachments")
            for attach in draft_data["attachments"]:
                src = draft_path / attach["relative_path"]
                dst = staging_pkg_path / "attachments" / attach["stored_filename"]
                shutil.copy2(src, dst)
                
        # 6. Final outbox destination path
        final_outbox_path = Path(self.settings.outbox_root) / pkg_name
        
        # 7. Finalize / Rename staging package to outbox
        # Ensures atomic folder write and raises error if outbox package exists
        self.storage.rename_or_finalize(staging_pkg_path, final_outbox_path)
        
        # 8. Clean up local draft
        self.delete_local_draft(draft_id)
        
        logger.info(f"Published draft '{draft_id}' as local message ID '{msg_id}' for user '{user_id_val}' targeting thread '{thread_id}'. Outbox path: {final_outbox_path}")
        return user_id_val, msg_id, str(final_outbox_path)

    # --- Thread & Receipt Reading API ---

    def list_active_threads(self) -> List[Dict[str, Any]]:
        """Scans the thread root for active threads and parses their configs/READMEs."""
        threads = []
        if not self.storage.exists(self.settings.thread_root):
            return []
            
        entries = self.storage.list_dir(self.settings.thread_root)
        for name in entries:
            thread_path = Path(self.settings.thread_root) / name
            if self.storage.is_dir(thread_path):
                meta_path = thread_path / "thread.json"
                if self.storage.exists(meta_path):
                    try:
                        meta = json.loads(self.storage.read_file_text(meta_path))
                        # Check README-like description
                        readme_path = thread_path / "README.md"
                        if self.storage.exists(readme_path):
                            meta["description_md"] = self.storage.read_file_text(readme_path)
                        threads.append(meta)
                    except Exception:
                        pass
        return threads

    def read_thread_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        """Reads all distributed messages in a thread folder, transparently reading from zip if archived."""
        thread_messages_path = Path(self.settings.thread_root) / thread_id / "messages"
        if not self.storage.exists(thread_messages_path):
            archive_path = Path(self.settings.archive_root) / f"{thread_id}.zip"
            if self.storage.exists(archive_path):
                import zipfile
                import collections
                messages = []
                try:
                    with zipfile.ZipFile(str(archive_path)) as zf:
                        msg_folders = collections.defaultdict(dict)
                        for name in zf.namelist():
                            parts = name.split("/")
                            if len(parts) >= 2 and parts[0] == thread_id:
                                parts = parts[1:]
                            if len(parts) >= 3 and parts[0] == "messages":
                                folder_name = parts[1]
                                sub_file = parts[2]
                                if sub_file == "message.json":
                                    msg_folders[folder_name]["meta"] = zf.read(name).decode("utf-8")
                                elif sub_file == "body.md":
                                    msg_folders[folder_name]["body"] = zf.read(name).decode("utf-8")
                        
                        for folder_name, contents in msg_folders.items():
                            if "meta" in contents:
                                try:
                                    meta = json.loads(contents["meta"])
                                    meta["body"] = contents.get("body", "")
                                    meta["folder_name"] = folder_name
                                    messages.append(meta)
                                except Exception:
                                    pass
                    messages.sort(key=lambda m: m.get("distributed_counter", 0))
                    return messages
                except Exception as e:
                    logger.error(f"Error reading messages from zip: {e}")
                    return []
            return []
            
        messages = []
        entries = self.storage.list_dir(thread_messages_path)
        for name in sorted(entries):
            msg_path = thread_messages_path / name
            if self.storage.is_dir(msg_path):
                meta_path = msg_path / "message.json"
                body_path = msg_path / "body.md"
                if self.storage.exists(meta_path) and self.storage.exists(body_path):
                    try:
                        meta = json.loads(self.storage.read_file_text(meta_path))
                        meta["body"] = self.storage.read_file_text(body_path)
                        meta["folder_name"] = name
                        messages.append(meta)
                    except Exception:
                        pass
        return messages

    def list_archived_threads(self) -> List[Dict[str, Any]]:
        """Scans the archive root for zipped threads and parses their metadata."""
        threads = []
        if not self.storage.exists(self.settings.archive_root):
            return []
            
        import zipfile
        entries = self.storage.list_dir(self.settings.archive_root)
        for name in entries:
            if name.endswith(".zip") and name.startswith("T_"):
                thread_id = name[:-4]
                zip_path = Path(self.settings.archive_root) / name
                try:
                    with zipfile.ZipFile(str(zip_path)) as zf:
                        possible_paths = ["thread.json", f"{thread_id}/thread.json"]
                        found_path = None
                        for p in possible_paths:
                            if p in zf.namelist():
                                found_path = p
                                break
                                
                        if found_path:
                            meta = json.loads(zf.read(found_path).decode("utf-8"))
                            readme_path = found_path.replace("thread.json", "README.md")
                            if readme_path in zf.namelist():
                                meta["description_md"] = zf.read(readme_path).decode("utf-8")
                            meta["status"] = "ARCHIVED"
                            threads.append(meta)
                except Exception:
                    pass
        return threads

    def list_receipts(self) -> List[Dict[str, Any]]:
        """Lists all receipts received from the coordinator."""
        receipts = []
        if not self.storage.exists(self.settings.receipt_root):
            return []
            
        entries = self.storage.list_dir(self.settings.receipt_root)
        for name in entries:
            if name.endswith(".json"):
                try:
                    content = self.storage.read_file_text(Path(self.settings.receipt_root) / name)
                    receipts.append(json.loads(content))
                except Exception:
                    pass
        return receipts

    def list_pending_outbox(self) -> List[Dict[str, Any]]:
        """Lists unsorted outbox message packages (pending coordinator distribution)."""
        pending = []
        if not self.storage.exists(self.settings.outbox_root):
            return []
            
        entries = self.storage.list_dir(self.settings.outbox_root)
        for name in entries:
            meta_path = Path(self.settings.outbox_root) / name / "message.json"
            if self.storage.exists(meta_path):
                try:
                    meta = json.loads(self.storage.read_file_text(meta_path))
                    meta["folder_name"] = name
                    pending.append(meta)
                except Exception:
                    pass
        return pending
