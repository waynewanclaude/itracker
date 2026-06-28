import os
import getpass
from pathlib import Path
from pydantic import BaseModel, Field

class Settings(BaseModel):
    user_id: str = Field(default_factory=getpass.getuser)
    role: str = Field(default="")
    display_name: str = Field(default="")
    root_dir: str = Field(default=r"G:\My Drive\shikibo_test")
    
    # Path configuration
    local_draft_root: str = Field(default="")
    outbox_root: str = Field(default="")
    receipt_root: str = Field(default="")
    thread_root: str = Field(default="")
    index_root: str = Field(default="")
    archive_root: str = Field(default="")
    
    # Coordinator configuration
    scan_interval: int = Field(default=5)  # seconds between background scans
    use_fs_events: bool = Field(default=True)
    
    # Logging configuration
    log_file: str = Field(default=r"C:\temp\shikibo.log")

    def model_post_init(self, __context) -> None:
        if not self.display_name:
            self.display_name = self.user_id
            
        root = Path(self.root_dir).resolve()
        
        # Build default paths if not explicitly overridden
        if self.role:
            if not self.local_draft_root:
                self.local_draft_root = str(root / "drafts" / self.user_id / self.role)
            if not self.outbox_root:
                self.outbox_root = str(root / "users" / self.user_id / self.role / "outbox")
            if not self.receipt_root:
                self.receipt_root = str(root / "users" / self.user_id / self.role / "receipts")
        else:
            if not self.local_draft_root:
                self.local_draft_root = str(root / "drafts" / self.user_id)
            if not self.outbox_root:
                self.outbox_root = str(root / "users" / self.user_id / "outbox")
            if not self.receipt_root:
                self.receipt_root = str(root / "users" / self.user_id / "receipts")
        if not self.thread_root:
            self.thread_root = str(root / "system" / "threads")
        if not self.index_root:
            self.index_root = str(root / "system" / "index")
        if not self.archive_root:
            self.archive_root = str(root / "system" / "archive")

def load_settings(config_path: str = None) -> Settings:
    """Load settings from environment variables, an optional JSON config file, or defaults."""
    data = {}
    if config_path and os.path.exists(config_path):
        import json
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load config from {config_path}: {e}")
            
    # Allow environment variable overrides
    env_keys = [
        "user_id", "role", "display_name", "root_dir", 
        "local_draft_root", "outbox_root", "receipt_root", 
        "thread_root", "index_root", "archive_root", "scan_interval",
        "use_fs_events", "log_file"
    ]
    for key in env_keys:
        env_val = os.environ.get(f"SHIKIBO_{key.upper()}")
        if env_val is not None:
            if key == "use_fs_events":
                data[key] = env_val.lower() in ("true", "1", "yes")
            else:
                data[key] = env_val
            
    return Settings(**data)

def setup_logging(settings: Settings) -> None:
    """Configures the root logger to write to both stdout and the configured log file path."""
    import logging
    log_path = Path(settings.log_file)
    try:
        os.makedirs(log_path.parent, exist_ok=True)
    except Exception as e:
        print(f"Warning: Failed to create log directory {log_path.parent}: {e}")
        
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handlers = []
    
    try:
        file_handler = logging.FileHandler(str(log_path), encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except Exception as e:
        print(f"Warning: Failed to create log file handler for {log_path}: {e}")
        
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    
    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
        force=True
    )
