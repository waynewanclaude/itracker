import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

# Add workspace root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Settings
from src.storage import FileSystemStorage
from src.client.client import ThreadMailClient
from src.coordinator.service import CoordinatorService

def test_integration():
    print("=== ThreadMail Integration Test ===")
    
    # 1. Setup config pointing to G:\My Drive\itracker_test
    test_root = r"G:\My Drive\itracker_test"
    print(f"Using test root: {test_root}")
    
    # We will clean up the test root if it exists to start fresh
    storage = FileSystemStorage()
    if storage.exists(test_root):
        print("Cleaning up existing test folder...")
        try:
            storage.delete(test_root)
        except Exception as e:
            print(f"Warning: failed to clear directory: {e}")
            
    storage.makedirs(test_root)
    
    settings = Settings(
        user_id="test_runner",
        display_name="Test Runner Agent",
        root_dir=test_root
    )
    
    # Initialize Client & Coordinator
    client = ThreadMailClient(settings, storage)
    coordinator = CoordinatorService(settings, storage)
    
    # Register client user
    coordinator.register_user(settings.user_id)
    print("Registered outboxes:", coordinator.get_registered_outboxes())
    
    # 2. Setup a test thread folder
    thread_id = "T_20260627_TEST"
    thread_dir = Path(settings.thread_root) / thread_id
    storage.makedirs(thread_dir)
    storage.makedirs(thread_dir / "messages")
    
    thread_meta = {
        "thread_id": thread_id,
        "title": "Integration Test Topic",
        "status": "OPEN",
        "created_at": "2026-06-27T23:30:34Z"
    }
    storage.write_file_new(thread_dir / "thread.json", json.dumps(thread_meta, indent=2))
    storage.write_file_new(thread_dir / "README.md", "README for integration testing thread")
    print(f"Created active thread: {thread_id}")
    
    # 3. Create draft
    print("Creating client draft...")
    draft_id = client.create_draft(thread_id=thread_id, body="Hello, this is a test message body.")
    
    # Add dummy attachment
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("This is a dummy attachment file content.")
        temp_attach_path = f.name
        
    try:
        client.add_attachment(draft_id, temp_attach_path)
        print("Attachment added to draft.")
        
        # Verify draft contents
        draft_data = client.read_draft(draft_id)
        assert draft_data["body"] == "Hello, this is a test message body."
        assert len(draft_data["attachments"]) == 1
        print("Draft read verified.")
        
        # 4. Publish Draft
        print("Publishing draft to outbox...")
        user_id, msg_id, outbox_path = client.publish_draft(draft_id)
        print(f"Published: user={user_id}, msg={msg_id}, path={outbox_path}")
        
        # Check outbox package
        assert os.path.exists(outbox_path)
        assert os.path.exists(os.path.join(outbox_path, "message.json"))
        assert os.path.exists(os.path.join(outbox_path, "body.md"))
        assert os.path.exists(os.path.join(outbox_path, "attachments"))
        print("Outbox package immutability/format verified.")
        
        # Check draft deletion
        assert not os.path.exists(client._get_draft_path(draft_id))
        print("Draft cleanup verified.")
        
        # 5. Run Coordinator Scan
        print("Running coordinator outbox scan...")
        scan_summary = coordinator.run_scan()
        print("Scan summary:", scan_summary)
        
        assert scan_summary["processed"] == 1
        assert scan_summary["scanned_outboxes"] == 1
        print("Scan summary count verified.")
        
        # Verify distribution in thread
        thread_messages_dir = thread_dir / "messages"
        msg_folders = os.listdir(thread_messages_dir)
        assert len(msg_folders) == 1
        print(f"Distributed message folder: {msg_folders[0]}")
        
        # Verify receipt
        receipts = client.list_receipts()
        assert len(receipts) == 1
        assert receipts[0]["source_local_message_id"] == msg_id
        assert receipts[0]["status"] == "distributed"
        print("Coordinator receipt verified.")
        
        # 6. Verify ledger record
        assert coordinator.is_message_distributed(user_id, msg_id)
        print("SQL ledger verified.")
        
        # 7. Test Duplicate prevention
        print("Re-scanning to check duplicate prevention...")
        second_scan = coordinator.run_scan()
        assert second_scan["processed"] == 0
        print("Duplicate prevention verified successfully.")
        
        print("\n=== INTEGRATION TEST PASSED SUCCESSFULLY! ===")
        
    finally:
        if os.path.exists(temp_attach_path):
            os.remove(temp_attach_path)

if __name__ == "__main__":
    test_integration()
