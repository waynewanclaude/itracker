import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

# Add workspace root to python path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shikibo.config import Settings
from shikibo.storage import FileSystemStorage
from shikibo.client.client import ThreadMailClient
from shikibo.coordinator.service import CoordinatorService

def test_integration():
    print("=== ThreadMail Integration Test ===")
    
    import uuid
    run_id = uuid.uuid4().hex[:8]
    test_user_id = f"test_runner_{run_id}"
    
    # 1. Setup config pointing to G:\My Drive\shikibo_test
    test_root = os.path.join(r"G:\My Drive\shikibo_test", f"run_{run_id}")
    print(f"Using test root: {test_root}")
    
    storage = FileSystemStorage()
    storage.makedirs(test_root)
    
    settings = Settings(
        user_id=test_user_id,
        display_name=f"Test Runner {run_id}",
        root_dir=test_root
    )
    
    # Initialize Client & Coordinator
    client = ThreadMailClient(settings, storage)
    coordinator = CoordinatorService(settings, storage)
    
    # Register client user (simulating admin manual registration)
    users_file = Path(settings.root_dir) / "system" / "config" / "registered_users.txt"
    storage.makedirs(users_file.parent)
    if storage.exists(users_file):
        storage.delete(users_file)
    storage.write_file_new(users_file, f"{settings.user_id}\n")
    storage.makedirs(settings.outbox_root)
    storage.makedirs(settings.receipt_root)
    print("Registered outboxes:", coordinator.get_registered_outboxes())
    
    # 2. Setup a test thread folder
    thread_id = f"T_TEST_{run_id}"
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
    
    # 2b. Test Empty Message rejection
    print("Testing empty message rejection...")
    from shikibo.client.client import BAD_VALUE
    empty_draft_id = client.create_draft(thread_id=thread_id, body="   ")
    try:
        client.publish_draft(empty_draft_id)
        assert False, "Should have failed to publish empty message"
    except BAD_VALUE as e:
        print(f"Success: Empty message publishing check raised BAD_VALUE as expected: {e}")
    finally:
        client.delete_local_draft(empty_draft_id)

    # 3. Create draft
    print("Creating client draft...")
    draft_id = client.create_draft(thread_id=thread_id, body="Hello @test_receiver, this is a test message body.")
    
    # Add dummy attachment
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("This is a dummy attachment file content.")
        temp_attach_path = f.name
        
    try:
        client.add_attachment(draft_id, temp_attach_path)
        print("Attachment added to draft.")
        
        # Verify draft contents
        draft_data = client.read_draft(draft_id)
        assert draft_data["body"] == "Hello @test_receiver, this is a test message body."
        assert len(draft_data["attachments"]) == 1
        print("Draft read verified.")
        
        # 4. Publish Draft
        print("Publishing draft to outbox...")
        user_id, msg_id, outbox_path = client.publish_draft(draft_id)
        print(f"Published: user={user_id}, msg={msg_id}, path={outbox_path}")
        
        # Check outbox package
        assert os.path.exists(outbox_path)
        with open(os.path.join(outbox_path, "message.json"), "r") as mf:
            meta = json.load(mf)
            assert meta["mentions"] == ["test_receiver"]
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
        
        # 7.1 Test temporary files and directories exclusion
        print("Testing temporary file/directory exclusion...")
        temp_dot_outbox = os.path.join(settings.outbox_root, ".temp_pkg")
        storage.makedirs(temp_dot_outbox)
        storage.write_file_new(os.path.join(temp_dot_outbox, "message.json"), "{}")
        
        temp_tmp_file = os.path.join(settings.outbox_root, "temp_pkg.tmp")
        storage.write_file_new(temp_tmp_file, "{}")
        
        temp_tilde_file = os.path.join(settings.outbox_root, "~temp_pkg")
        storage.write_file_new(temp_tilde_file, "{}")

        temp_scan = coordinator.run_scan()
        assert temp_scan["processed"] == 0
        assert temp_scan["dead_lettered"] == 0
        
        storage.delete(temp_dot_outbox)
        storage.delete(temp_tmp_file)
        storage.delete(temp_tilde_file)
        print("Temporary file/directory exclusion verified successfully.")
        
        # 8. Test Role-based publishing and scanning
        print("Testing Role-based flow...")
        role_settings = Settings(
            user_id=test_user_id,
            role="developer",
            display_name=f"Test Developer {run_id}",
            root_dir=test_root
        )
        role_client = ThreadMailClient(role_settings, storage)
        
        # Verify role-specific draft, outbox, and receipt paths
        assert "developer" in role_settings.local_draft_root
        assert "developer" in role_settings.outbox_root
        assert "developer" in role_settings.receipt_root
        
        # Create role draft
        role_draft_id = role_client.create_draft(thread_id=thread_id, body="This is from developer role.")
        
        # Publish role draft
        r_user_id, r_msg_id, r_outbox_path = role_client.publish_draft(role_draft_id)
        assert r_user_id == f"{test_user_id}/developer"
        assert os.path.exists(r_outbox_path)
        
        # The registered user is the top-level user 'test_user_id'
        # Check that get_registered_outboxes includes the role outbox
        registered_outboxes = [os.path.normpath(p) for p in coordinator.get_registered_outboxes()]
        assert os.path.normpath(role_settings.outbox_root) in registered_outboxes
        print("Role outbox detected in registration.")
        
        # Scan outboxes
        role_scan = coordinator.run_scan()
        if role_scan["processed"] != 1:
            print("DEBUG: role_scan is", role_scan)
        assert role_scan["processed"] == 1
        
        # Check distributed message on thread folder
        msg_folders_updated = os.listdir(thread_messages_dir)
        role_folders = [f for f in msg_folders_updated if "developer" in f]
        assert len(role_folders) == 1
        print(f"Distributed role message folder: {role_folders[0]}")
        
        # Verify receipt written in role receipt path
        role_receipts = role_client.list_receipts()
        assert len(role_receipts) == 1
        assert role_receipts[0]["source_user_id"] == f"{test_user_id}/developer"
        
        # Verify deduplication
        role_scan_dup = coordinator.run_scan()
        assert role_scan_dup["processed"] == 0
        print("Role-based flow verified successfully.")
        
        print("\n=== INTEGRATION TEST PASSED SUCCESSFULLY! ===")
        
    finally:
        if os.path.exists(temp_attach_path):
            os.remove(temp_attach_path)
        try:
            storage.delete(test_root)
        except Exception as e:
            print(f"Warning: Failed to delete test root: {e}")

def test_coordinator_locks():
    print("=== Testing Coordinator Locks and Authorization ===")
    import socket
    import getpass
    import uuid
    
    run_id = uuid.uuid4().hex[:8]
    test_root = os.path.join(r"G:\My Drive\shikibo_test", f"run_lock_{run_id}")
    storage = FileSystemStorage()
    storage.makedirs(test_root)
    
    settings = Settings(
        user_id="lock_test_user",
        root_dir=test_root
    )
    
    # 1. Test missing coordinator_host.json raises SystemExit
    hostname = socket.gethostname()
    pid = os.getpid()
    status_file = Path(settings.root_dir) / "system" / "coordinator" / f"{hostname}-{pid}.txt"

    try:
        coord = CoordinatorService(settings, storage)
        coord.enforce_service_locks()
        assert False, "Should have failed due to missing coordinator_host.json"
    except SystemExit as e:
        print("Success: Missing host config check raised SystemExit as expected.")
        assert "configuration file is missing" in str(e)
        assert storage.exists(status_file)
        status_content = storage.read_file_text(status_file)
        assert "Exit:" in status_content
        assert "missing" in status_content
        
        # Check GMT timestamp (ends with 'Z')
        timestamp_line = status_content.splitlines()[0]
        assert timestamp_line.endswith("Z")
        print("Success: GMT timestamp format ending with 'Z' verified.")
        print("Success: Missing host status file verified.")
        
    # 2. Test mismatched coordinator_host.json raises SystemExit
    host_file = Path(settings.root_dir) / "system" / "config" / "coordinator_host.json"
    storage.makedirs(host_file.parent)
    storage.write_file_new(host_file, json.dumps({"host": "wrong_host", "user": "wrong_user"}))
    
    try:
        coord = CoordinatorService(settings, storage)
        coord.enforce_service_locks()
        assert False, "Should have failed due to mismatched host/user"
    except SystemExit as e:
        print("Success: Mismatched host config check raised SystemExit as expected.")
        assert "Unauthorized host/user configuration" in str(e)
        assert storage.exists(status_file)
        status_content = storage.read_file_text(status_file)
        assert "Exit:" in status_content
        assert "Unauthorized host/user" in status_content
        print("Success: Mismatched host status file verified.")
        
    # 3. Test matching host config works and claims the throne (writes PID)
    storage.delete(host_file)
    storage.write_file_new(host_file, json.dumps({"host": socket.gethostname(), "user": getpass.getuser()}))
    
    # Write dummy old status file to test cleanup
    dummy_status_file = Path(settings.root_dir) / "system" / "coordinator" / f"{hostname}-9999.txt"
    storage.write_file_new(dummy_status_file, "dummy content")
    assert storage.exists(dummy_status_file)

    coord1 = CoordinatorService(settings, storage)
    coord1.enforce_service_locks()
    print("Success: Coordinator instantiated with valid host config.")
    assert not storage.exists(dummy_status_file)
    print("Success: Old status files cleanup verified.")
    
    pid_file = Path(settings.root_dir) / "system" / "coordinator" / "coordinator_pid.txt"
    assert storage.exists(pid_file)
    assert int(storage.read_file_text(pid_file).strip()) == os.getpid()
    print("Success: PID file correctly written with current PID.")
    
    # 4. Test double instantiation in the same process does not fail (reclaims throne)
    coord2 = CoordinatorService(settings, storage)
    coord2.enforce_service_locks()
    print("Success: Re-instantiation in same process succeeded without error.")
    
    # 5. Test another active process raises SystemExit
    # We write the parent process ID (which is running Python/runner and contains "python")
    try:
        parent_pid = os.getppid()
        storage.delete(pid_file)
        storage.write_file_new(pid_file, str(parent_pid))
        
        coord3 = CoordinatorService(settings, storage)
        coord3.enforce_service_locks()
        assert False, f"Should have failed due to active coordinator PID check (PID: {parent_pid})"
    except SystemExit as e:
        print("Success: Active coordinator PID collision check raised SystemExit as expected.")
        assert "already running" in str(e)
        assert storage.exists(status_file)
        status_content = storage.read_file_text(status_file)
        assert "Exit:" in status_content
        assert "already running" in status_content
        print("Success: PID collision status file verified.")
        
    # Clean up test root
    try:
        storage.delete(test_root)
    except Exception:
        pass
    print("=== LOCK TESTS PASSED SUCCESSFULLY! ===")

def test_settings_validation():
    print("=== Testing Settings Defaults and Validation ===")
    from shikibo.config import Settings, BAD_VALUE
    import getpass
    import socket
    
    # 1. Fallback default user_id and role when empty/missing
    s1 = Settings()
    expected_user = f"{getpass.getuser()}@{socket.gethostname()}"
    assert s1.user_id == expected_user
    assert s1.role == "__DEF__"
    print("Success: Settings fallback user_id and role verified.")
    
    # 2. Length check (must be 4 or above)
    try:
        Settings(user_id="abc")
        assert False, "Should have failed due to short user_id"
    except BAD_VALUE as e:
        print("Success: Short user_id correctly rejected:", e)
        
    try:
        Settings(role="xyz")
        assert False, "Should have failed due to short role"
    except BAD_VALUE as e:
        print("Success: Short role correctly rejected:", e)
        
    # 3. Reserved prefix/suffix starts/ends with __ for user defined names
    try:
        Settings(user_id="__myuser")
        assert False, "Should have failed due to system reserved user_id prefix"
    except BAD_VALUE as e:
        print("Success: Reserved user_id prefix correctly rejected:", e)
        
    try:
        Settings(user_id="myuser__")
        assert False, "Should have failed due to system reserved user_id suffix"
    except BAD_VALUE as e:
        print("Success: Reserved user_id suffix correctly rejected:", e)
        
    try:
        Settings(role="__myrole")
        assert False, "Should have failed due to system reserved role prefix"
    except BAD_VALUE as e:
        print("Success: Reserved role prefix correctly rejected:", e)
        
    try:
        Settings(role="myrole__")
        assert False, "Should have failed due to system reserved role suffix"
    except BAD_VALUE as e:
        print("Success: Reserved role suffix correctly rejected:", e)
        
    # Explicitly using default role __DEF__ should be allowed
    s2 = Settings(role="__DEF__")
    assert s2.role == "__DEF__"
    print("Success: Explicit __DEF__ role allowed.")
    
    print("=== SETTINGS VALIDATION TESTS PASSED SUCCESSFULLY! ===")

def test_webapp_thread_api():
    print("=== Testing WebApp Thread API (Distributed ID & Duplicates) ===")
    from shikibo.webapp import app as webapp_module
    from shikibo.webapp.app import app
    from shikibo.config import load_settings
    from shikibo.storage import FileSystemStorage
    from shikibo.client.client import ThreadMailClient
    from shikibo.coordinator.service import CoordinatorService
    import shutil
    import os
    
    test_root = os.path.join(r"G:\My Drive\shikibo_test", "run_api_test")
    settings = load_settings(root_dir=test_root)
    storage = FileSystemStorage()
    storage.makedirs(test_root)
    
    webapp_module.settings = settings
    webapp_module.storage = storage
    webapp_module.client = ThreadMailClient(settings, storage)
    webapp_module.coordinator = CoordinatorService(settings, storage)
    
    with app.test_client() as client:
        res = client.get("/api/threads/next-id?title=My%20Test%20Thread")
        assert res.status_code == 200
        data = res.get_json()
        thread_id = data["thread_id"]
        assert thread_id.startswith("T_")
        assert len(thread_id) == 130 # 'T_' + 128 hex digits
        print("Success: Generated thread ID format verified.")
        
        res = client.post("/api/threads", json={
            "thread_id": thread_id,
            "title": "My Test Thread",
            "description": "My test thread desc"
        })
        assert res.status_code == 200
        print("Success: Thread created successfully via API.")
        
        res = client.post("/api/threads", json={
            "thread_id": thread_id,
            "title": "Another title",
            "description": "Another desc"
        })
        assert res.status_code == 400
        print("Success: Duplicate thread ID correctly rejected with 400.")
        
        # Clean up
        thread_dir = Path(settings.thread_root) / thread_id
        if os.path.exists(thread_dir):
            shutil.rmtree(thread_dir)
        try:
            storage.delete(test_root)
        except Exception:
            pass

if __name__ == "__main__":
    test_integration()
    test_coordinator_locks()
    test_settings_validation()
    test_webapp_thread_api()
