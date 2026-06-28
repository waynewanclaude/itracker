import os
import sys
import time
import argparse
from pathlib import Path
from rich.console import Console
from rich.table import Table

# Add workspace root to python path to prevent import issues
from shikibo.config import load_settings
from shikibo.storage import FileSystemStorage
from shikibo.webapp.app import run_server
from shikibo.coordinator.service import CoordinatorService

console = Console()

def run_single_scan(settings, storage):
    console.print("[bold blue]Starting Coordinator Outbox Scan...[/bold blue]")
    coordinator = CoordinatorService(settings, storage)
    summary = coordinator.run_scan()
    
    table = Table(title="Coordinator Scan Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green")
    
    table.add_row("Scanned Outboxes", str(summary["scanned_outboxes"]))
    table.add_row("Processed Packages", str(summary["processed"]))
    table.add_row("Duplicates Skipped", str(summary["duplicates"]))
    table.add_row("Dead Lettered", str(summary["dead_lettered"]))
    
    console.print(table)
    
    if summary["errors"]:
        console.print("[bold red]Errors/Warnings encountered:[/bold red]")
        for err in summary["errors"]:
            console.print(f" - {err}", style="red")

def run_coordinator_service(settings, storage):
    coordinator = CoordinatorService(settings, storage)
    if settings.use_fs_events:
        console.print("[bold green]Starting Coordinator filesystem event service...[/bold green]")
        console.print("[yellow]Press Ctrl+C to stop.[/yellow]")
        coordinator.start_fs_watcher()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[bold red]Coordinator service stopped by user.[/bold red]")
        finally:
            coordinator.stop_fs_watcher()
    else:
        console.print(f"[bold green]Starting Coordinator Daemon Service (Interval: {settings.scan_interval}s)...[/bold green]")
        console.print("[yellow]Press Ctrl+C to stop.[/yellow]")
        while True:
            try:
                summary = coordinator.run_scan()
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                console.print(f"[{ts}] Scan completed. Processed: {summary['processed']}, Duplicates: {summary['duplicates']}, Dead-letters: {summary['dead_lettered']}")
                time.sleep(settings.scan_interval)
            except KeyboardInterrupt:
                console.print("\n[bold red]Coordinator service stopped by user.[/bold red]")
                break
            except Exception as e:
                console.print(f"[bold red]Error in scan loop: {e}[/bold red]")
                time.sleep(5)

def run_archive_thread(thread_id, settings, storage):
    console.print(f"[bold blue]Archiving thread: {thread_id}...[/bold blue]")
    coordinator = CoordinatorService(settings, storage)
    success = coordinator.archive_thread(thread_id)
    if success:
        console.print(f"[bold green]Thread {thread_id} successfully archived to archive/T_{thread_id}.zip[/bold green]")
    else:
        console.print(f"[bold red]Failed to archive thread {thread_id}. Check if thread directory exists or has already been archived.[/bold red]")

def main():
    parser = argparse.ArgumentParser(description="Distributed ThreadMail System CLI Orchestrator")
    parser.add_argument("-c", "--config", help="Path to config JSON file")
    parser.add_argument("-r", "--root-dir", help="Override testing root directory (defaults to G:\\My Drive\\shikibo_test)")
    parser.add_argument("-u", "--user", help="Override user identity (defaults to system user)")
    parser.add_argument("--role", help="Override user role (optional)")
    parser.add_argument("--poll-only", action="store_true", help="Disable filesystem events and use periodic polling instead")
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # WebApp parser
    webapp_parser = subparsers.add_parser("webapp", help="Launch the minimalist local WebApp UI")
    webapp_parser.add_argument("-p", "--port", type=int, default=5000, help="Port to run the WebApp (default: 5000)")
    webapp_parser.add_argument("-d", "--debug", action="store_true", help="Run Flask in debug mode")
    
    # Scan parser
    subparsers.add_parser("scan", help="Run a single one-shot coordinator scan")
    
    # Service parser
    service_parser = subparsers.add_parser("service", help="Run coordinator as a timed background daemon service")
    service_parser.add_argument("-i", "--interval", type=int, help="Override background scan interval in seconds")
    
    # Archive parser
    archive_parser = subparsers.add_parser("archive", help="Archive a thread folder into a ZIP package")
    archive_parser.add_argument("thread_id", help="Thread ID to archive")
    
    args = parser.parse_args()
    
    # Load settings with optional overrides
    settings = load_settings(args.config)
    if args.user:
        settings.user_id = args.user
    if args.role:
        settings.role = args.role
    if args.root_dir:
        settings.root_dir = args.root_dir
    if args.poll_only:
        settings.use_fs_events = False
    
    # Re-initialize path structure based on overrides
    settings.model_post_init(None)
        
    storage = FileSystemStorage()
    
    # Verify root testing folder exists or make it
    storage.makedirs(settings.root_dir)
    
    if args.command == "webapp":
        console.print(f"[bold green]Launching WebApp (requesting port {args.port}) as user '{settings.user_id}'" + (f" and role '{settings.role}'" if settings.role else "") + "...[/bold green]")
        # Set settings globally for Flask server loading
        os.environ["SHIKIBO_ROOT_DIR"] = settings.root_dir
        os.environ["SHIKIBO_USER_ID"] = settings.user_id
        if settings.role:
            os.environ["SHIKIBO_ROLE"] = settings.role
        if settings.use_fs_events:
            os.environ["SHIKIBO_USE_FS_EVENTS"] = "true"
        else:
            os.environ["SHIKIBO_USE_FS_EVENTS"] = "false"
        run_server(port=args.port, debug=args.debug)
        
    elif args.command == "scan":
        run_single_scan(settings, storage)
        
    elif args.command == "service":
        if args.interval:
            settings.scan_interval = args.interval
        run_coordinator_service(settings, storage)
        
    elif args.command == "archive":
        run_archive_thread(args.thread_id, settings, storage)
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
