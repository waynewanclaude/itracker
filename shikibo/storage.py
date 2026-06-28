import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Union

class FileSystemStorage:
    """An abstraction layer for filesystem-like sync operations.
    
    This abstracts path checks and atomic operations, ensuring that files 
    are written safely and do not overwrite existing outbox packages.
    """
    
    def exists(self, path: Union[str, Path]) -> bool:
        return os.path.exists(path)
        
    def is_dir(self, path: Union[str, Path]) -> bool:
        return os.path.isdir(path)
        
    def list_dir(self, path: Union[str, Path]) -> List[str]:
        """Returns a list of entry names in the directory."""
        if not self.exists(path):
            return []
        return os.listdir(path)
        
    def read_file_text(self, path: Union[str, Path]) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
            
    def read_file_bytes(self, path: Union[str, Path]) -> bytes:
        with open(path, "rb") as f:
            return f.read()
            
    def makedirs(self, path: Union[str, Path]) -> None:
        try:
            os.makedirs(path, exist_ok=True)
        except FileExistsError:
            pass
        except OSError:
            if not os.path.exists(path):
                raise
        
    def write_file_new(self, path: Union[str, Path], content: Union[str, bytes]) -> None:
        """Writes content to a path. Raises FileExistsError if the file already exists.
        Uses an atomic write (write to temp, then rename) to ensure robustness.
        """
        path = Path(path)
        if self.exists(path):
            raise FileExistsError(f"Target file already exists: {path}")
            
        self.makedirs(path.parent)
        
        # Write to a temporary file in the same directory to ensure atomic rename
        with tempfile.NamedTemporaryFile("w" if isinstance(content, str) else "wb", 
                                         dir=path.parent, 
                                         delete=False, 
                                         encoding="utf-8" if isinstance(content, str) else None) as temp:
            temp_path = temp.name
            try:
                temp.write(content)
            except Exception:
                os.remove(temp_path)
                raise
                
        import time
        retries = 5
        for i in range(retries):
            try:
                # Atomic rename/move. Will fail if target exists (which we checked above)
                os.replace(temp_path, path)
                break
            except PermissionError:
                if i == retries - 1:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    raise
                time.sleep(0.5)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise

    def copy_tree(self, src: Union[str, Path], dst: Union[str, Path]) -> None:
        """Recursively copies a directory tree. Refuses overwrite if dst exists."""
        if self.exists(dst):
            raise FileExistsError(f"Destination already exists: {dst}")
        self.makedirs(Path(dst).parent)
        shutil.copytree(src, dst)
        
    def rename_or_finalize(self, src: Union[str, Path], dst: Union[str, Path]) -> None:
        """Atomic rename. Raises FileExistsError if destination exists."""
        if self.exists(dst):
            raise FileExistsError(f"Destination already exists: {dst}")
        self.makedirs(Path(dst).parent)
        
        import time
        retries = 5
        for i in range(retries):
            try:
                os.replace(src, dst)
                break
            except PermissionError:
                if i == retries - 1:
                    raise
                time.sleep(0.5)
        
    def delete(self, path: Union[str, Path]) -> None:
        """Deletes a file or directory recursively with retries for open/locked files."""
        if not self.exists(path):
            return
            
        import time
        retries = 5
        for i in range(retries):
            try:
                if self.is_dir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                break
            except (PermissionError, OSError) as e:
                if i == retries - 1:
                    raise
                time.sleep(0.5)
