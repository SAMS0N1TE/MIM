# chat_window.py
import sys
import os
import datetime
import re
from sound_utils import play_sound_async
from pathlib import Path
# ********************************************************
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

# --- Helper function ---
def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- Function to sanitize filenames ---
def sanitize_filename(filename):

    if not filename: return "_invalid_id_" 
    sanitized = re.sub(r'[\\/*?:"<>|!]', '_', filename)
    sanitized = sanitized.strip('. ') 
    if not sanitized: sanitized = "_sanitized_empty_"
    return sanitized

class ChatWindow(QMainWindow):
    """
    Represents the individual chat window for an IM conversation. Handles optional conversation logging.
    """
    closing = Signal(str) # Pass buddy ID when closing
    message_sent = Signal(str, str) # Pass recipient_id, message_text

    def __init__(self, my_screen_name, buddy_id, auto_save_enabled=False, logs_base_dir=None):
        super().__init__()
        self.my_screen_name = my_screen_name
        self.buddy_id = buddy_id
        self.auto_save_enabled = auto_save_enabled
        self.log_file_path = None # Initialize log path

        if self.auto_save_enabled and logs_base_dir and self.buddy_id:
            try:
                log_dir = Path(logs_base_dir)
                log_dir.mkdir(parents=True, exist_ok=True)
                safe_buddy_id = sanitize_filename(self.buddy_id)
                self.log_file_path = log_dir / f"{safe_buddy_id}.log" # Store as Path object
                print(f"[ChatWindow] Logging enabled for {self.buddy_id} -> {self.log_file_path}")
            except Exception as e:
                print(f"[ChatWindow] ERROR setting up log path for {self.buddy_id}: {e}")
                self.auto_save_enabled = False
        if not self.auto_save_enabled:
             print(f"[ChatWindow] Logging disabled for {self.buddy_id}. AutoSave={auto_save_enabled}, BaseDir={logs_base_dir}")

        self.setWindowTitle(f"IM with {self.buddy_id}")
        self.setMinimumSize(400, 350)

        # --- Central Widget and Layout ---
        central_widget = QWidget(); self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget); main_layout.setContentsMargins(5, 5, 5, 5); main_layout.setSpacing(3)

        # --- Message Display Area ---
        self.message_display = QTextEdit(); self.message_display.setReadOnly(True); main_layout.addWidget(self.message_display, 1)

        # --- Formatting Toolbar ---
        self.formatting_toolbar = QToolBar("Formatting"); self.formatting_toolbar.setStyleSheet("QToolBar { border: none; padding: 1px; }")

        # --- Message Input Area & Send Button ---
        input_layout = QHBoxLayout(); input_layout.setSpacing(3)
        self.message_input = QTextEdit(); self.message_input.setFixedHeight(60); self.message_input.installEventFilter(self)
        self.send_button = QPushButton(); send_icon=QIcon(get_resource_path("resources/icons/send_icon.png"))
        if not send_icon.isNull(): self.send_button.setIcon(send_icon); self.send_button.setIconSize(QSize(24, 24)); self.send_button.setFixedSize(QSize(32, 32)); self.send_button.setToolTip("Send (Enter)"); self.send_button.setStyleSheet("QPushButton{padding:2px;}")
        else: self.send_button.setText("Send")
        input_layout.addWidget(self.message_input, 1); input_layout.addWidget(self.send_button)

        main_layout.addWidget(self.formatting_toolbar); main_layout.addLayout(input_layout)

        self._create_menu_bar(); self._create_format_actions(); self._populate_formatting_toolbar(self.formatting_toolbar)

        # --- Connect Signals ---
        self.message_input.currentCharFormatChanged.connect(self.update_format_button_states)
        self.send_button.clicked.connect(self.send_message)

        # --- Status Bar ---
        self.statusBar().showMessage(f"Chatting with {self.buddy_id}")

        # --- Set Initial Formatting & Load History ---
        self._set_default_formatting()
        self._load_history()

    def _set_default_formatting(self):
        dfam="Helvetica"; dsize=10; avail=QFontDatabase.families()
        if dfam not in avail: print(f"Warn:'{dfam}' not found."); self.current_font=QFont(); self.current_font.setPointSize(dsize)
        else: self.current_font=QFont(dfam,dsize)
        self.current_color=Qt.black; self.message_input.setCurrentFont(self.current_font); self.message_input.setTextColor(self.current_color)
        if hasattr(self,'bold_action'): self.update_format_button_states(self.message_input.currentCharFormat())
        else: QTimer.singleShot(0, lambda:self.update_format_button_states(self.message_input.currentCharFormat()))

    def _create_menu_bar(self): menu_bar=self.menuBar(); file_menu=menu_bar.addMenu("&File"); save_action=QAction("&Save Conversation...",self); save_action.setEnabled(False); close_action=QAction("&Close",self); close_action.setShortcut(QKeySequence.Close); close_action.triggered.connect(self.close); file_menu.addAction(save_action); file_menu.addSeparator(); file_menu.addAction(close_action); edit_menu=menu_bar.addMenu("&Edit"); undo_action=QAction("&Undo",self); undo_action.setShortcut(QKeySequence.Undo); undo_action.triggered.connect(self.message_input.undo); redo_action=QAction("&Redo",self); redo_action.setShortcut(QKeySequence.Redo); redo_action.triggered.connect(self.message_input.redo); cut_action=QAction("Cu&t",self); cut_action.setShortcut(QKeySequence.Cut); cut_action.triggered.connect(self.message_input.cut); copy_action=QAction("&Copy",self); copy_action.setShortcut(QKeySequence.Copy); copy_action.triggered.connect(self.message_input.copy); paste_action=QAction("&Paste",self); paste_action.setShortcut(QKeySequence.Paste); paste_action.triggered.connect(self.message_input.paste); select_all_action=QAction("Select &All",self); select_all_action.setShortcut(QKeySequence.SelectAll); select_all_action.triggered.connect(self.message_input.selectAll); edit_menu.addAction(undo_action); edit_menu.addAction(redo_action); edit_menu.addSeparator(); edit_menu.addAction(cut_action); edit_menu.addAction(copy_action); edit_menu.addAction(paste_action); edit_menu.addSeparator(); edit_menu.addAction(select_all_action); view_menu=menu_bar.addMenu("&View"); away_msg_action=QAction("Away &Message...",self); away_msg_action.setEnabled(False); view_menu.addAction(away_msg_action); people_menu=menu_bar.addMenu("&People"); get_info_action=QAction("&Get Info...",self); get_info_action.setEnabled(False); block_action=QAction("&Block...",self); block_action.setEnabled(False); warn_action=QAction("&Warn...",self); warn_action.setEnabled(False); people_menu.addAction(get_info_action); people_menu.addSeparator(); people_menu.addAction(block_action); people_menu.addAction(warn_action)
    def _create_format_actions(self): base=get_resource_path("resources/icons/"); fi=QIcon(os.path.join(base,"font.png")); self.font_action=QAction(fi,"&Font...",self); self.font_action.setToolTip("Font"); self.font_action.triggered.connect(self.select_font); ci=QIcon(os.path.join(base,"color.png")); self.color_action=QAction(ci,"&Color...",self); self.color_action.setToolTip("Color"); self.color_action.triggered.connect(self.select_color); bi=QIcon(os.path.join(base,"bold.png")); self.bold_action=QAction(bi,"&Bold",self); self.bold_action.setShortcut(QKeySequence.Bold); self.bold_action.setCheckable(True); self.bold_action.setToolTip("Bold"); self.bold_action.triggered.connect(self.toggle_bold); ii=QIcon(os.path.join(base,"italic.png")); self.italic_action=QAction(ii,"&Italic",self); self.italic_action.setShortcut(QKeySequence.Italic); self.italic_action.setCheckable(True); self.italic_action.setToolTip("Italic"); self.italic_action.triggered.connect(self.toggle_italic); ui=QIcon(os.path.join(base,"underline.png")); self.underline_action=QAction(ui,"&Underline",self); self.underline_action.setShortcut(QKeySequence.Underline); self.underline_action.setCheckable(True); self.underline_action.setToolTip("Underline"); self.underline_action.triggered.connect(self.toggle_underline); li=QIcon(os.path.join(base,"link.png")); self.link_action=QAction(li,"&Link...",self); self.link_action.setToolTip("Link"); self.link_action.triggered.connect(self.insert_link_placeholder); si=QIcon(os.path.join(base,"smiley.png")); self.smiley_action=QAction(si,"&Smiley...",self); self.smiley_action.setToolTip("Smiley"); self.smiley_action.triggered.connect(self.insert_smiley_placeholder)
    def _populate_formatting_toolbar(self,tb): tb.addAction(self.font_action); tb.addAction(self.color_action); tb.addSeparator(); tb.addAction(self.bold_action); tb.addAction(self.italic_action); tb.addAction(self.underline_action); tb.addSeparator(); tb.addAction(self.link_action); tb.addAction(self.smiley_action)

    # --- Formatting Action Handlers ---
    def select_font(self): ok, font = QFontDialog.getFont(self.message_input.currentFont(), self); fmt=QTextCharFormat(); fmt.setFont(font); self.merge_format_on_selection(fmt) if ok else None
    def select_color(self): color = QColorDialog.getColor(self.message_input.textColor(), self); fmt=QTextCharFormat(); fmt.setForeground(color); self.merge_format_on_selection(fmt) if color.isValid() else None
    def toggle_bold(self): self.set_selected_text_format("bold", self.bold_action.isChecked())
    def toggle_italic(self): self.set_selected_text_format("italic", self.italic_action.isChecked())
    def toggle_underline(self): self.set_selected_text_format("underline", self.underline_action.isChecked())


    def insert_link_placeholder(self):

        text, ok = QInputDialog.getText(self, 'Insert Link', 'Enter URL:', QLineEdit.Normal, "http://")
        if ok and text: 
            cursor = self.message_input.textCursor()
            if cursor.isNull(): return 

            # --- Apply link format ---
            link_fmt = QTextCharFormat()
            link_fmt.setAnchor(True)
            link_fmt.setAnchorHref(text)
            link_fmt.setForeground(Qt.blue)
            link_fmt.setFontUnderline(True)

            # Apply format to selection or insert new text
            if cursor.hasSelection():
                 cursor.mergeCharFormat(link_fmt)
                 cursor.clearSelection()

                 cursor.movePosition(QTextCursor.EndOfBlock if cursor.atBlockEnd() else QTextCursor.NextCharacter)
            else:
                 cursor.insertText(text, link_fmt)


            default_fmt = QTextCharFormat()
            default_fmt.setAnchor(False)
            default_fmt.setFontUnderline(False)

            default_fmt.setForeground(self.current_color)
            default_fmt.setFont(self.current_font)
            self.message_input.setCurrentCharFormat(default_fmt)
            self.message_input.setFocus()


    def insert_smiley_placeholder(self): cursor=self.message_input.textCursor(); cursor.insertText(" :) ") if not cursor.isNull() else None; self.message_input.setFocus()
    def set_selected_text_format(self,p,v): fmt=QTextCharFormat(); (fmt.setFontWeight(QFont.Bold if v else QFont.Normal) if p=="bold" else (fmt.setFontItalic(v) if p=="italic" else fmt.setFontUnderline(v) if p=="underline" else None)); self.merge_format_on_selection(fmt)
    def merge_format_on_selection(self,fmt): cursor=self.message_input.textCursor(); cursor.mergeCharFormat(fmt); self.message_input.mergeCurrentCharFormat(fmt); self.message_input.setFocus() if not cursor.isNull() else None
    def update_format_button_states(self,fmt): b=fmt.fontWeight()==QFont.Bold; i=fmt.fontItalic(); u=fmt.fontUnderline(); self.bold_action.blockSignals(True); self.bold_action.setChecked(b); self.bold_action.blockSignals(False) if hasattr(self,'bold_action') else None; self.italic_action.blockSignals(True); self.italic_action.setChecked(i); self.italic_action.blockSignals(False) if hasattr(self,'italic_action') else None; self.underline_action.blockSignals(True); self.underline_action.setChecked(u); self.underline_action.blockSignals(False) if hasattr(self,'underline_action') else None

    # --- Message Handling ---
    def format_message(self, who, message_text, color=None, font=None):
        display_font = font if font else QFont("Helvetica", 10);
        if "Helvetica" not in QFontDatabase.families(): display_font = QFont("Arial", 10)
        display_color = color if color is not None else (Qt.red if who != self.my_screen_name else self.message_input.textColor())
        clr, fam, sz, un = QColor(display_color).name(), display_font.family(), display_font.pointSize(), "pt"
        fw, fs, td = ('bold' if display_font.bold() else 'normal'), ('italic' if display_font.italic() else 'normal'), ('underline' if display_font.underline() else 'none')
        style = f"font-family:'{fam}'; font-size:{sz}{un}; color:{clr}; font-weight:{fw}; font-style:{fs}; text-decoration:{td};"
        safe_who = who.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        esc_msg = message_text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('\n','<br>')
        return f'<span style="{style}"><b>{safe_who}:</b> {esc_msg}</span>'

    def _save_message(self, timestamp, sender, message_text):
        if not self.auto_save_enabled or not self.log_file_path: return
        try:
            log_line = f"[{timestamp.isoformat()}] {sender}: {message_text}\n"
            print(f"[ChatWindow] Attempting to save to {self.log_file_path}")
            with open(self.log_file_path, 'a', encoding='utf-8') as f: f.write(log_line)
        except IOError as e: print(f"[ChatWindow] Error writing to log file {self.log_file_path}: {e}")
        except Exception as e: print(f"[ChatWindow] Unexpected error saving log: {e}")

    def _load_history(self):
        if not self.auto_save_enabled or not self.log_file_path: return
        if not self.log_file_path.exists(): print(f"[ChatWindow] Log file not found: {self.log_file_path}"); return
        print(f"[ChatWindow] Loading history from: {self.log_file_path}")
        try:
            loaded_count = 0; log_content = self.log_file_path.read_text(encoding='utf-8')
            for line in log_content.splitlines():
                line = line.strip();
                if not line: continue
                match = re.match(r'^\[(.*?)\]\s+(.*?):\s+(.*)$', line)
                if match:
                    ts_str, sender, message = match.groups(); is_self = (sender == self.my_screen_name)
                    color = self.current_color if is_self else Qt.red
                    fmt_line = self.format_message(sender, message, color=color, font=self.current_font)
                    self.message_display.append(fmt_line); loaded_count += 1
                else: print(f"[ChatWindow] Could not parse log line: {line}")
            if loaded_count > 0: self.message_display.moveCursor(QTextCursor.End); print(f"[ChatWindow] Loaded {loaded_count} lines."); self.statusBar().showMessage(f"Loaded {loaded_count} history.", 3000)
            else: print("[ChatWindow] Log file empty/unparseable.")
        except IOError as e: print(f"[ChatWindow] Error reading log file {self.log_file_path}: {e}"); self.statusBar().showMessage("Error loading history.", 3000)
        except Exception as e: print(f"[ChatWindow] Unexpected error loading history: {e}"); self.statusBar().showMessage("Error loading history.", 3000)

    def send_message(self):
        message_text_plain = self.message_input.toPlainText().strip();
        if not message_text_plain: return
        current_input_format = self.message_input.currentCharFormat(); display_color = current_input_format.foreground().color(); display_font = current_input_format.font()
        formatted_msg = self.format_message(self.my_screen_name, message_text_plain, display_color, display_font)
        self.message_display.append(formatted_msg); self.message_display.moveCursor(QTextCursor.End)
        timestamp = datetime.datetime.now(datetime.timezone.utc); self._save_message(timestamp, self.my_screen_name, message_text_plain)
        self.message_sent.emit(self.buddy_id, message_text_plain); self.message_input.clear(); self.message_input.setFocus()
        print(f"Playing message sent sound for chat with {self.buddy_id}")
        play_sound_async("send.wav") # Message sent sound
        self.message_input.clear()
        self.message_input.setFocus()

    def receive_message(self, message_text):
        formatted_msg = self.format_message(self.buddy_id, message_text);
        self.message_display.append(formatted_msg); self.message_display.moveCursor(QTextCursor.End)
        timestamp = datetime.datetime.now(datetime.timezone.utc); self._save_message(timestamp, self.buddy_id, message_text)
        if not self.isActiveWindow(): QApplication.alert(self)

    # --- Event Filter & Close Event ---
    def eventFilter(self, obj, event):
        if obj is self.message_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if not (event.modifiers() & Qt.ShiftModifier): self.send_message(); return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        print(f"[ChatWindow] closeEvent for {self.buddy_id}."); self.closing.emit(self.buddy_id); event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    test_log_dir = Path("./test_chat_logs"); test_log_dir.mkdir(exist_ok=True)
    dummy_buddy_id = "test_buddy_!123"; dummy_log_file = test_log_dir / f"{sanitize_filename(dummy_buddy_id)}.log"
    try:
        with open(dummy_log_file, "w", encoding='utf-8') as f:
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat(); f.write(f"[{ts}] {dummy_buddy_id}: Previous message 1\n")
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat(); f.write(f"[{ts}] MyName: Previous message 2\n")
    except IOError as e: print(f"Could not write dummy log: {e}")
    chat_win = ChatWindow("MyName", dummy_buddy_id, auto_save_enabled=True, logs_base_dir=str(test_log_dir))
    chat_win.show()
    QTimer.singleShot(1000, lambda: chat_win.receive_message("Hello there! This is a test."))
    exit_code = app.exec()
    sys.exit(exit_code)