import sys
import os
import datetime
import re
import html
from sound_utils import play_sound_async
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QToolBar, QStatusBar, QSizePolicy, QApplication, QColorDialog,
    QFontDialog, QMenuBar, QMessageBox, QInputDialog
)
from PySide6.QtGui import (
    QAction, QIcon, QTextCursor, QColor, QFont, QKeySequence, QPixmap,
    QFontDatabase, QTextCharFormat
)
from PySide6.QtCore import Qt, Signal, QTimer, QEvent, QSize, QStandardPaths

PUBLIC_CHAT_ID = "^all"

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def sanitize_filename(filename):
    if not filename: return "_invalid_id_"
    sanitized = re.sub(r'[\\/*?:"<>|!^]', '_', filename)
    sanitized = sanitized.strip('. ')
    if not sanitized: sanitized = "_sanitized_empty_"
    return sanitized

class ChatWindow(QMainWindow):
    closing = Signal(str)
    message_sent = Signal(str, str)

    def __init__(self, my_screen_name, buddy_id, display_name, auto_save_enabled=False, logs_base_dir=None):
        super().__init__()
        self.my_screen_name = my_screen_name
        self.buddy_id = buddy_id
        self.display_name = display_name
        self.is_public_chat = (self.buddy_id == PUBLIC_CHAT_ID)
        self.auto_save_enabled = auto_save_enabled
        self.log_file_path = None

        if self.auto_save_enabled and logs_base_dir and self.buddy_id:
            try:
                log_dir = Path(logs_base_dir)
                log_dir.mkdir(parents=True, exist_ok=True)
                safe_buddy_id = sanitize_filename(self.buddy_id)
                self.log_file_path = log_dir / f"{safe_buddy_id}.log"
                print(f"[ChatWindow] Logging enabled for {self.display_name} ({self.buddy_id}) -> {self.log_file_path}")
            except Exception as e:
                print(f"[ChatWindow] ERROR setting up log path for {self.display_name} ({self.buddy_id}): {e}")
                self.auto_save_enabled = False
        if not self.auto_save_enabled:
             print(f"[ChatWindow] Logging disabled for {self.display_name} ({self.buddy_id}). AutoSave={auto_save_enabled}, BaseDir={logs_base_dir}")

        self.setWindowTitle(self.display_name)
        self.setMinimumSize(400, 350)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(3)

        self.message_display = QTextEdit()
        self.message_display.setReadOnly(True)
        main_layout.addWidget(self.message_display, 1)

        self.formatting_toolbar = QToolBar("Formatting")
        self.formatting_toolbar.setStyleSheet("QToolBar { border: none; padding: 1px; }")

        input_layout = QHBoxLayout()
        input_layout.setSpacing(3)
        self.message_input = QTextEdit()
        self.message_input.setFixedHeight(60)
        self.message_input.installEventFilter(self)
        self.send_button = QPushButton()
        send_icon=QIcon(get_resource_path("resources/icons/send_icon.png"))
        if not send_icon.isNull(): self.send_button.setIcon(send_icon); self.send_button.setIconSize(QSize(24, 24)); self.send_button.setFixedSize(QSize(32, 32)); self.send_button.setToolTip("Send (Enter)"); self.send_button.setStyleSheet("QPushButton{padding:2px;}")
        else: self.send_button.setText("Send")
        input_layout.addWidget(self.message_input, 1)
        input_layout.addWidget(self.send_button)

        main_layout.addWidget(self.formatting_toolbar)
        main_layout.addLayout(input_layout)

        self._create_menu_bar()
        self._create_format_actions()
        self._populate_formatting_toolbar(self.formatting_toolbar)

        self.message_input.currentCharFormatChanged.connect(self.update_format_button_states)
        self.send_button.clicked.connect(self.send_message)

        status_text = f"Public Chat" if self.is_public_chat else f"Chatting with {self.display_name} ({self.buddy_id})"
        self.statusBar().showMessage(status_text)

        self._set_default_formatting()
        self._load_history()

    def _set_default_formatting(self):
        default_font_family="Helvetica"; default_font_size=10
        available_families=QFontDatabase.families()
        if default_font_family not in available_families:
             print(f"Warn: Default font '{default_font_family}' not found. Using application default.")
             self.current_font=QFont()
             self.current_font.setPointSize(default_font_size)
        else:
             self.current_font=QFont(default_font_family, default_font_size)

        self.current_color=Qt.black
        self.message_input.setCurrentFont(self.current_font)
        self.message_input.setTextColor(self.current_color)
        QTimer.singleShot(0, lambda: self.update_format_button_states(self.message_input.currentCharFormat()))


    def _create_menu_bar(self):
        menu_bar=self.menuBar()
        file_menu=menu_bar.addMenu("&File")
        save_action=QAction("&Save Conversation...",self)
        save_action.triggered.connect(self.save_conversation_manually)
        save_action.setEnabled(not self.auto_save_enabled or bool(self.message_display.toPlainText()))
        self.message_display.textChanged.connect(lambda: save_action.setEnabled(not self.auto_save_enabled or bool(self.message_display.toPlainText())))

        close_action=QAction("&Close",self)
        close_action.setShortcut(QKeySequence.Close)
        close_action.triggered.connect(self.close)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        file_menu.addAction(close_action)

        edit_menu=menu_bar.addMenu("&Edit")
        undo_action=QAction("&Undo",self)
        undo_action.setShortcut(QKeySequence.Undo)
        undo_action.triggered.connect(self.message_input.undo)
        redo_action=QAction("&Redo",self)
        redo_action.setShortcut(QKeySequence.Redo)
        redo_action.triggered.connect(self.message_input.redo)
        cut_action=QAction("Cu&t",self)
        cut_action.setShortcut(QKeySequence.Cut)
        cut_action.triggered.connect(self.message_input.cut)
        copy_action=QAction("&Copy",self)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(self.message_input.copy)
        paste_action=QAction("&Paste",self)
        paste_action.setShortcut(QKeySequence.Paste)
        paste_action.triggered.connect(self.message_input.paste)
        select_all_action=QAction("Select &All",self)
        select_all_action.setShortcut(QKeySequence.SelectAll)
        select_all_action.triggered.connect(self.message_input.selectAll)
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(cut_action)
        edit_menu.addAction(copy_action)
        edit_menu.addAction(paste_action)
        edit_menu.addSeparator()
        edit_menu.addAction(select_all_action)

    def _create_format_actions(self):
        icon_base = get_resource_path("resources/icons/")
        font_icon = QIcon(os.path.join(icon_base, "font.png"))
        self.font_action = QAction(font_icon, "&Font...", self)
        self.font_action.setToolTip("Font")
        self.font_action.triggered.connect(self.select_font)
        color_icon = QIcon(os.path.join(icon_base, "color.png"))
        self.color_action = QAction(color_icon, "&Color...", self)
        self.color_action.setToolTip("Color")
        self.color_action.triggered.connect(self.select_color)
        bold_icon = QIcon(os.path.join(icon_base, "bold.png"))
        self.bold_action = QAction(bold_icon, "&Bold", self)
        self.bold_action.setShortcut(QKeySequence.Bold)
        self.bold_action.setCheckable(True)
        self.bold_action.setToolTip("Bold")
        self.bold_action.triggered.connect(self.toggle_bold)
        italic_icon = QIcon(os.path.join(icon_base, "italic.png"))
        self.italic_action = QAction(italic_icon, "&Italic", self)
        self.italic_action.setShortcut(QKeySequence.Italic)
        self.italic_action.setCheckable(True)
        self.italic_action.setToolTip("Italic")
        self.italic_action.triggered.connect(self.toggle_italic)
        underline_icon = QIcon(os.path.join(icon_base, "underline.png"))
        self.underline_action = QAction(underline_icon, "&Underline", self)
        self.underline_action.setShortcut(QKeySequence.Underline)
        self.underline_action.setCheckable(True)
        self.underline_action.setToolTip("Underline")
        self.underline_action.triggered.connect(self.toggle_underline)
        link_icon = QIcon(os.path.join(icon_base, "link.png"))
        self.link_action = QAction(link_icon, "&Link...", self)
        self.link_action.setToolTip("Insert Link (Placeholder)")
        self.link_action.triggered.connect(self.insert_link_placeholder)
        smiley_icon = QIcon(os.path.join(icon_base, "smiley.png"))
        self.smiley_action = QAction(smiley_icon, "&Smiley...", self)
        self.smiley_action.setToolTip("Insert Smiley (Placeholder)")
        self.smiley_action.triggered.connect(self.insert_smiley_placeholder)

    def _populate_formatting_toolbar(self, toolbar):
        toolbar.addAction(self.font_action)
        toolbar.addAction(self.color_action)
        toolbar.addSeparator()
        toolbar.addAction(self.bold_action)
        toolbar.addAction(self.italic_action)
        toolbar.addAction(self.underline_action)
        toolbar.addSeparator()
        toolbar.addAction(self.link_action)
        toolbar.addAction(self.smiley_action)

    def select_font(self):
        ok, font = QFontDialog.getFont(self.message_input.currentFont(), self)
        if ok:
            fmt = QTextCharFormat()
            fmt.setFont(font)
            self.merge_format_on_selection(fmt)

    def select_color(self):
        color = QColorDialog.getColor(self.message_input.textColor(), self)
        if color.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            self.merge_format_on_selection(fmt)

    def toggle_bold(self): self.set_selected_text_format("bold", self.bold_action.isChecked())
    def toggle_italic(self): self.set_selected_text_format("italic", self.italic_action.isChecked())
    def toggle_underline(self): self.set_selected_text_format("underline", self.underline_action.isChecked())

    def insert_link_placeholder(self):
        cursor=self.message_input.textCursor()
        if not cursor.isNull(): cursor.insertText(" [link] ")
        self.message_input.setFocus()

    def insert_smiley_placeholder(self):
        cursor=self.message_input.textCursor()
        if not cursor.isNull(): cursor.insertText(" :) ")
        self.message_input.setFocus()

    def set_selected_text_format(self, property_name, value):
        fmt = QTextCharFormat()
        if property_name == "bold": fmt.setFontWeight(QFont.Bold if value else QFont.Normal)
        elif property_name == "italic": fmt.setFontItalic(value)
        elif property_name == "underline": fmt.setFontUnderline(value)
        self.merge_format_on_selection(fmt)

    def merge_format_on_selection(self, text_format):
        cursor = self.message_input.textCursor()
        if not cursor.isNull():
            cursor.mergeCharFormat(text_format)
            self.message_input.mergeCurrentCharFormat(text_format)
            self.message_input.setFocus()

    def update_format_button_states(self, current_format):
        is_bold = current_format.fontWeight() == QFont.Bold
        is_italic = current_format.fontItalic()
        is_underline = current_format.fontUnderline()

        if hasattr(self, 'bold_action'): self.bold_action.blockSignals(True); self.bold_action.setChecked(is_bold); self.bold_action.blockSignals(False)
        if hasattr(self, 'italic_action'): self.italic_action.blockSignals(True); self.italic_action.setChecked(is_italic); self.italic_action.blockSignals(False)
        if hasattr(self, 'underline_action'): self.underline_action.blockSignals(True); self.underline_action.setChecked(is_underline); self.underline_action.blockSignals(False)

    def format_message(self, sender_display_name, message_text, color=None, font=None):
        display_font = font if font else self.current_font
        display_color = color if color is not None else (Qt.blue if sender_display_name != self.my_screen_name else self.current_color)

        clr_name = QColor(display_color).name()
        family = display_font.family()
        size = display_font.pointSize()
        weight = 'bold' if display_font.bold() else 'normal'
        style = 'italic' if display_font.italic() else 'normal'
        decoration = 'underline' if display_font.underline() else 'none'

        safe_sender = html.escape(sender_display_name)
        safe_message = html.escape(message_text).replace('\n', '<br>')

        style_attr = f"font-family:'{family}'; font-size:{size}pt; color:{clr_name}; font-weight:{weight}; font-style:{style}; text-decoration:{decoration};"
        formatted_html = f'<span style="{style_attr}"><b>{safe_sender}:</b> {safe_message}</span>'
        return formatted_html

    def _save_message(self, timestamp, sender, message_text):
        if not self.auto_save_enabled or not self.log_file_path: return
        try:
            log_line = f"[{timestamp.isoformat()}] {sender}: {message_text}\n"
            with open(self.log_file_path, 'a', encoding='utf-8') as f: f.write(log_line)
        except IOError as e: print(f"[ChatWindow] Error writing to log file {self.log_file_path}: {e}")
        except Exception as e: print(f"[ChatWindow] Unexpected error saving log: {e}")

    def _load_history(self):
        if not self.auto_save_enabled or not self.log_file_path: return
        if not self.log_file_path.exists(): print(f"[ChatWindow] Log file not found: {self.log_file_path}"); return

        print(f"[ChatWindow] Loading history from: {self.log_file_path}")
        loaded_count = 0
        try:
            log_content = self.log_file_path.read_text(encoding='utf-8')
            for line in log_content.splitlines():
                line = line.strip()
                if not line: continue
                match = re.match(r'^\[(.*?)\]\s+(.*?):\s+(.*)$', line)
                if match:
                    ts_str, sender, message = match.groups()
                    is_self = (sender == self.my_screen_name)
                    color = self.current_color if is_self else Qt.blue
                    fmt_line = self.format_message(sender, message, color=color, font=self.current_font)
                    self.message_display.append(fmt_line)
                    loaded_count += 1
                else:
                    print(f"[ChatWindow] Could not parse log line: {line}")

            if loaded_count > 0:
                 self.message_display.moveCursor(QTextCursor.End)
                 print(f"[ChatWindow] Loaded {loaded_count} lines.")
                 self.statusBar().showMessage(f"Loaded {loaded_count} history messages.", 3000)
            else: print("[ChatWindow] Log file empty or unparseable.")
        except IOError as e: print(f"[ChatWindow] Error reading log file {self.log_file_path}: {e}"); self.statusBar().showMessage("Error loading history.", 3000)
        except Exception as e: print(f"[ChatWindow] Unexpected error loading history: {e}"); self.statusBar().showMessage("Error loading history.", 3000)

    def save_conversation_manually(self):
        from PySide6.QtWidgets import QFileDialog
        if not self.message_display.toPlainText():
            QMessageBox.information(self, "Save Conversation", "Nothing to save.")
            return

        default_filename = sanitize_filename(self.display_name) + ".html"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Conversation As...", default_filename, "HTML Files (*.html);;Text Files (*.txt)")

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    if file_path.endswith('.html'):
                        f.write("<html><head><title>Chat Log</title></head><body>\n")
                        f.write(self.message_display.toHtml())
                        f.write("\n</body></html>")
                    else:
                        f.write(self.message_display.toPlainText())
                self.statusBar().showMessage(f"Conversation saved to {os.path.basename(file_path)}", 3000)
            except IOError as e:
                QMessageBox.warning(self, "Save Error", f"Could not save conversation:\n{e}")

    def send_message(self):
        message_text_plain = self.message_input.toPlainText().strip()
        if not message_text_plain: return

        current_input_format = self.message_input.currentCharFormat()
        display_color = current_input_format.foreground().color()
        display_font = current_input_format.font()

        formatted_msg_html = self.format_message(self.my_screen_name, message_text_plain, display_color, display_font)
        self.message_display.append(formatted_msg_html)
        self.message_display.moveCursor(QTextCursor.End)

        timestamp = datetime.datetime.now(datetime.timezone.utc)
        self._save_message(timestamp, self.my_screen_name, message_text_plain)

        self.message_sent.emit(self.buddy_id, message_text_plain)

        play_sound_async("send.wav")

        self.message_input.clear()
        self.message_input.setFocus()

    # --- FIX: Updated parameter name and usage ---
    def receive_message(self, message_text, sender_display_name=None):
        # Determine the display name for the sender
        # If sender_display_name is provided use it, otherwise use self.display_name (for direct IMs where only buddy_id is known initially)
        actual_sender_display = sender_display_name if sender_display_name else self.display_name

        formatted_msg_html = self.format_message(actual_sender_display, message_text, color=Qt.blue, font=self.current_font)
        self.message_display.append(formatted_msg_html)
        self.message_display.moveCursor(QTextCursor.End)

        timestamp = datetime.datetime.now(datetime.timezone.utc)
        self._save_message(timestamp, actual_sender_display, message_text)

        if not self.isActiveWindow():
            QApplication.alert(self)

    def eventFilter(self, watched_object, event):
        if watched_object is self.message_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if not (event.modifiers() & Qt.ShiftModifier):
                    self.send_message()
                    return True
        return super().eventFilter(watched_object, event)

    def closeEvent(self, event):
        print(f"[ChatWindow] closeEvent for {self.buddy_id}.")
        self.closing.emit(self.buddy_id)
        event.accept()
