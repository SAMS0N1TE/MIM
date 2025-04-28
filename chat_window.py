# chat_window.py
import sys
import os
import datetime
import re
import html # For escaping HTML in messages
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

PUBLIC_CHAT_ID = "^all" # Consistent definition

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
    """Sanitizes a string to be safe for use as a filename."""
    if not filename: return "_invalid_id_"
    # Replace problematic characters with underscores
    sanitized = re.sub(r'[\\/*?:"<>|!^]', '_', filename) # Added ^
    sanitized = sanitized.strip('. ') # Remove leading/trailing dots/spaces
    if not sanitized: sanitized = "_sanitized_empty_" # Handle case where all chars were removed
    return sanitized

class ChatWindow(QMainWindow):
    """
    Represents the individual chat window for an IM or Public Chat conversation.
    Handles optional conversation logging.
    """
    closing = Signal(str) # Pass buddy ID (^all or !nodeid) when closing
    message_sent = Signal(str, str) # Pass recipient_id (^all or !nodeid), message_text

    # MODIFIED: Added display_name parameter to __init__
    def __init__(self, my_screen_name, buddy_id, display_name, auto_save_enabled=False, logs_base_dir=None):
        super().__init__()
        self.my_screen_name = my_screen_name
        self.buddy_id = buddy_id # This is the actual ID (!...) or PUBLIC_CHAT_ID
        self.display_name = display_name # Use the passed display name
        self.is_public_chat = (self.buddy_id == PUBLIC_CHAT_ID)
        self.auto_save_enabled = auto_save_enabled
        self.log_file_path = None # Initialize log path

        # --- Setup Logging ---
        if self.auto_save_enabled and logs_base_dir and self.buddy_id:
            try:
                log_dir = Path(logs_base_dir)
                log_dir.mkdir(parents=True, exist_ok=True)
                # Use sanitized buddy_id for the filename
                safe_buddy_id = sanitize_filename(self.buddy_id)
                self.log_file_path = log_dir / f"{safe_buddy_id}.log" # Store as Path object
                print(f"[ChatWindow] Logging enabled for {self.display_name} ({self.buddy_id}) -> {self.log_file_path}")
            except Exception as e:
                print(f"[ChatWindow] ERROR setting up log path for {self.display_name} ({self.buddy_id}): {e}")
                self.auto_save_enabled = False # Disable logging if setup fails
        if not self.auto_save_enabled:
             print(f"[ChatWindow] Logging disabled for {self.display_name} ({self.buddy_id}). AutoSave={auto_save_enabled}, BaseDir={logs_base_dir}")

        # Use the display_name (passed during init) for the window title
        self.setWindowTitle(self.display_name)
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
        else: self.send_button.setText("Send") # Fallback text
        input_layout.addWidget(self.message_input, 1); input_layout.addWidget(self.send_button)

        main_layout.addWidget(self.formatting_toolbar); main_layout.addLayout(input_layout)

        # --- Create UI Elements ---
        self._create_menu_bar(); self._create_format_actions(); self._populate_formatting_toolbar(self.formatting_toolbar)

        # --- Connect Signals ---
        self.message_input.currentCharFormatChanged.connect(self.update_format_button_states)
        self.send_button.clicked.connect(self.send_message)

        # --- Status Bar ---
        status_text = f"Public Chat" if self.is_public_chat else f"Chatting with {self.display_name} ({self.buddy_id})"
        self.statusBar().showMessage(status_text)

        # --- Set Initial Formatting & Load History ---
        self._set_default_formatting()
        self._load_history()

    def _set_default_formatting(self):
        """Sets the default font and color for the input field."""
        default_font_family="Helvetica"; default_font_size=10
        available_families=QFontDatabase.families()
        if default_font_family not in available_families:
             print(f"Warn: Default font '{default_font_family}' not found. Using application default.");
             self.current_font=QFont() # Use application default
             self.current_font.setPointSize(default_font_size)
        else:
             self.current_font=QFont(default_font_family, default_font_size)

        self.current_color=Qt.black
        self.message_input.setCurrentFont(self.current_font)
        self.message_input.setTextColor(self.current_color)
        # Update toolbar state after setting defaults (use timer for safety)
        QTimer.singleShot(0, lambda: self.update_format_button_states(self.message_input.currentCharFormat()))


    def _create_menu_bar(self):
        """Creates the main menu bar for the chat window."""
        menu_bar=self.menuBar()
        # --- File Menu ---
        file_menu=menu_bar.addMenu("&File")
        save_action=QAction("&Save Conversation...",self)
        save_action.triggered.connect(self.save_conversation_manually)
        # Only enable manual save if auto-save is NOT enabled, or if there's something to save
        save_action.setEnabled(not self.auto_save_enabled or bool(self.message_display.toPlainText()))
        self.message_display.textChanged.connect(lambda: save_action.setEnabled(not self.auto_save_enabled or bool(self.message_display.toPlainText())))

        close_action=QAction("&Close",self)
        close_action.setShortcut(QKeySequence.Close)
        close_action.triggered.connect(self.close)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        file_menu.addAction(close_action)

        # --- Edit Menu ---
        edit_menu=menu_bar.addMenu("&Edit")
        undo_action=QAction("&Undo",self); undo_action.setShortcut(QKeySequence.Undo); undo_action.triggered.connect(self.message_input.undo)
        redo_action=QAction("&Redo",self); redo_action.setShortcut(QKeySequence.Redo); redo_action.triggered.connect(self.message_input.redo)
        cut_action=QAction("Cu&t",self); cut_action.setShortcut(QKeySequence.Cut); cut_action.triggered.connect(self.message_input.cut)
        copy_action=QAction("&Copy",self); copy_action.setShortcut(QKeySequence.Copy); copy_action.triggered.connect(self.message_input.copy)
        paste_action=QAction("&Paste",self); paste_action.setShortcut(QKeySequence.Paste); paste_action.triggered.connect(self.message_input.paste)
        select_all_action=QAction("Select &All",self); select_all_action.setShortcut(QKeySequence.SelectAll); select_all_action.triggered.connect(self.message_input.selectAll)
        edit_menu.addAction(undo_action); edit_menu.addAction(redo_action); edit_menu.addSeparator(); edit_menu.addAction(cut_action); edit_menu.addAction(copy_action); edit_menu.addAction(paste_action); edit_menu.addSeparator(); edit_menu.addAction(select_all_action)

        # --- View/People Menus (Placeholder/Disabled) ---
        # view_menu=menu_bar.addMenu("&View"); away_msg_action=QAction("Away &Message...",self); away_msg_action.setEnabled(False); view_menu.addAction(away_msg_action)
        # people_menu=menu_bar.addMenu("&People"); get_info_action=QAction("&Get Info...",self); get_info_action.setEnabled(False); block_action=QAction("&Block...",self); block_action.setEnabled(False); warn_action=QAction("&Warn...",self); warn_action.setEnabled(False); people_menu.addAction(get_info_action); people_menu.addSeparator(); people_menu.addAction(block_action); people_menu.addAction(warn_action)

    def _create_format_actions(self):
        """Creates QAction objects for text formatting."""
        icon_base = get_resource_path("resources/icons/")
        # Font Action
        font_icon = QIcon(os.path.join(icon_base, "font.png"))
        self.font_action = QAction(font_icon, "&Font...", self)
        self.font_action.setToolTip("Font")
        self.font_action.triggered.connect(self.select_font)
        # Color Action
        color_icon = QIcon(os.path.join(icon_base, "color.png"))
        self.color_action = QAction(color_icon, "&Color...", self)
        self.color_action.setToolTip("Color")
        self.color_action.triggered.connect(self.select_color)
        # Bold Action
        bold_icon = QIcon(os.path.join(icon_base, "bold.png"))
        self.bold_action = QAction(bold_icon, "&Bold", self)
        self.bold_action.setShortcut(QKeySequence.Bold)
        self.bold_action.setCheckable(True)
        self.bold_action.setToolTip("Bold")
        self.bold_action.triggered.connect(self.toggle_bold)
        # Italic Action
        italic_icon = QIcon(os.path.join(icon_base, "italic.png"))
        self.italic_action = QAction(italic_icon, "&Italic", self)
        self.italic_action.setShortcut(QKeySequence.Italic)
        self.italic_action.setCheckable(True)
        self.italic_action.setToolTip("Italic")
        self.italic_action.triggered.connect(self.toggle_italic)
        # Underline Action
        underline_icon = QIcon(os.path.join(icon_base, "underline.png"))
        self.underline_action = QAction(underline_icon, "&Underline", self)
        self.underline_action.setShortcut(QKeySequence.Underline)
        self.underline_action.setCheckable(True)
        self.underline_action.setToolTip("Underline")
        self.underline_action.triggered.connect(self.toggle_underline)
        # Link Action (Placeholder functionality)
        link_icon = QIcon(os.path.join(icon_base, "link.png"))
        self.link_action = QAction(link_icon, "&Link...", self)
        self.link_action.setToolTip("Insert Link (Placeholder)")
        self.link_action.triggered.connect(self.insert_link_placeholder)
        # Smiley Action (Placeholder functionality)
        smiley_icon = QIcon(os.path.join(icon_base, "smiley.png"))
        self.smiley_action = QAction(smiley_icon, "&Smiley...", self)
        self.smiley_action.setToolTip("Insert Smiley (Placeholder)")
        self.smiley_action.triggered.connect(self.insert_smiley_placeholder)

    def _populate_formatting_toolbar(self, toolbar):
        """Adds the format actions to the toolbar."""
        toolbar.addAction(self.font_action)
        toolbar.addAction(self.color_action)
        toolbar.addSeparator()
        toolbar.addAction(self.bold_action)
        toolbar.addAction(self.italic_action)
        toolbar.addAction(self.underline_action)
        toolbar.addSeparator()
        toolbar.addAction(self.link_action)
        toolbar.addAction(self.smiley_action)

    # --- Formatting Action Handlers ---
    def select_font(self):
        """Opens font dialog and applies selected font."""
        ok, font = QFontDialog.getFont(self.message_input.currentFont(), self)
        if ok:
            fmt = QTextCharFormat(); fmt.setFont(font)
            self.merge_format_on_selection(fmt)

    def select_color(self):
        """Opens color dialog and applies selected color."""
        color = QColorDialog.getColor(self.message_input.textColor(), self)
        if color.isValid():
            fmt = QTextCharFormat(); fmt.setForeground(color)
            self.merge_format_on_selection(fmt)

    def toggle_bold(self): self.set_selected_text_format("bold", self.bold_action.isChecked())
    def toggle_italic(self): self.set_selected_text_format("italic", self.italic_action.isChecked())
    def toggle_underline(self): self.set_selected_text_format("underline", self.underline_action.isChecked())

    def insert_link_placeholder(self):
        """Placeholder to insert a link."""
        cursor=self.message_input.textCursor()
        if not cursor.isNull(): cursor.insertText(" [link] ")
        self.message_input.setFocus()

    def insert_smiley_placeholder(self):
        """Placeholder to insert a smiley."""
        cursor=self.message_input.textCursor();
        if not cursor.isNull(): cursor.insertText(" :) ")
        self.message_input.setFocus()

    def set_selected_text_format(self, property_name, value):
        """Applies bold, italic, or underline format."""
        fmt = QTextCharFormat()
        if property_name == "bold": fmt.setFontWeight(QFont.Bold if value else QFont.Normal)
        elif property_name == "italic": fmt.setFontItalic(value)
        elif property_name == "underline": fmt.setFontUnderline(value)
        self.merge_format_on_selection(fmt)

    def merge_format_on_selection(self, text_format):
        """Applies format to current selection or sets for future typing."""
        cursor = self.message_input.textCursor()
        if not cursor.isNull():
            cursor.mergeCharFormat(text_format) # Apply to selection
            self.message_input.mergeCurrentCharFormat(text_format) # Set for future typing
            self.message_input.setFocus()

    def update_format_button_states(self, current_format):
        """Updates the check state of format buttons based on cursor position."""
        is_bold = current_format.fontWeight() == QFont.Bold
        is_italic = current_format.fontItalic()
        is_underline = current_format.fontUnderline()

        # Block signals to prevent recursive calls when setting checked state
        if hasattr(self, 'bold_action'): self.bold_action.blockSignals(True); self.bold_action.setChecked(is_bold); self.bold_action.blockSignals(False)
        if hasattr(self, 'italic_action'): self.italic_action.blockSignals(True); self.italic_action.setChecked(is_italic); self.italic_action.blockSignals(False)
        if hasattr(self, 'underline_action'): self.underline_action.blockSignals(True); self.underline_action.setChecked(is_underline); self.underline_action.blockSignals(False)

    # --- Message Handling ---
    def format_message(self, sender_display_name, message_text, color=None, font=None):
        """Formats a message line as HTML for display."""
        # Use defaults if not provided
        display_font = font if font else self.current_font # Use window's current font if none specified
        display_color = color if color is not None else (Qt.blue if sender_display_name != self.my_screen_name else self.current_color) # Blue for others, input color for self

        # Get font properties for styling
        clr_name = QColor(display_color).name()
        family = display_font.family()
        size = display_font.pointSize()
        weight = 'bold' if display_font.bold() else 'normal'
        style = 'italic' if display_font.italic() else 'normal'
        decoration = 'underline' if display_font.underline() else 'none'

        # Escape sender name and message text to prevent HTML injection
        safe_sender = html.escape(sender_display_name)
        # Convert newlines to <br> and escape other HTML chars
        safe_message = html.escape(message_text).replace('\n', '<br>')

        # Construct HTML string
        style_attr = f"font-family:'{family}'; font-size:{size}pt; color:{clr_name}; font-weight:{weight}; font-style:{style}; text-decoration:{decoration};"
        formatted_html = f'<span style="{style_attr}"><b>{safe_sender}:</b> {safe_message}</span>'
        return formatted_html

    def _save_message(self, timestamp, sender, message_text):
        """Appends a message line to the log file if enabled."""
        if not self.auto_save_enabled or not self.log_file_path: return
        try:
            # Simple text log format
            log_line = f"[{timestamp.isoformat()}] {sender}: {message_text}\n"
            # Append to log file
            with open(self.log_file_path, 'a', encoding='utf-8') as f: f.write(log_line)
        except IOError as e: print(f"[ChatWindow] Error writing to log file {self.log_file_path}: {e}")
        except Exception as e: print(f"[ChatWindow] Unexpected error saving log: {e}")

    def _load_history(self):
        """Loads chat history from the log file."""
        if not self.auto_save_enabled or not self.log_file_path: return
        if not self.log_file_path.exists(): print(f"[ChatWindow] Log file not found: {self.log_file_path}"); return

        print(f"[ChatWindow] Loading history from: {self.log_file_path}")
        loaded_count = 0
        try:
            log_content = self.log_file_path.read_text(encoding='utf-8')
            for line in log_content.splitlines():
                line = line.strip()
                if not line: continue
                # Parse the simple log format
                match = re.match(r'^\[(.*?)\]\s+(.*?):\s+(.*)$', line)
                if match:
                    ts_str, sender, message = match.groups()
                    is_self = (sender == self.my_screen_name)
                    # Use different colors for self vs others
                    color = self.current_color if is_self else Qt.blue # Or Qt.red, etc.
                    # Use the default font for loaded history
                    fmt_line = self.format_message(sender, message, color=color, font=self.current_font)
                    self.message_display.append(fmt_line)
                    loaded_count += 1
                else:
                    print(f"[ChatWindow] Could not parse log line: {line}")

            if loaded_count > 0:
                 self.message_display.moveCursor(QTextCursor.End) # Scroll to bottom
                 print(f"[ChatWindow] Loaded {loaded_count} lines.")
                 self.statusBar().showMessage(f"Loaded {loaded_count} history messages.", 3000)
            else: print("[ChatWindow] Log file empty or unparseable.")
        except IOError as e: print(f"[ChatWindow] Error reading log file {self.log_file_path}: {e}"); self.statusBar().showMessage("Error loading history.", 3000)
        except Exception as e: print(f"[ChatWindow] Unexpected error loading history: {e}"); self.statusBar().showMessage("Error loading history.", 3000)

    def save_conversation_manually(self):
        """Saves the current conversation display to a file (basic HTML)."""
        from PySide6.QtWidgets import QFileDialog
        if not self.message_display.toPlainText():
            QMessageBox.information(self, "Save Conversation", "Nothing to save.")
            return

        # Suggest a filename
        default_filename = sanitize_filename(self.display_name) + ".html"
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Conversation As...", default_filename, "HTML Files (*.html);;Text Files (*.txt)")

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    if file_path.endswith('.html'):
                        f.write("<html><head><title>Chat Log</title></head><body>\n")
                        f.write(self.message_display.toHtml()) # Save as HTML
                        f.write("\n</body></html>")
                    else:
                        f.write(self.message_display.toPlainText()) # Save as plain text
                self.statusBar().showMessage(f"Conversation saved to {os.path.basename(file_path)}", 3000)
            except IOError as e:
                QMessageBox.warning(self, "Save Error", f"Could not save conversation:\n{e}")

    def send_message(self):
        """Formats, displays, saves, and emits the message typed by the user."""
        message_text_plain = self.message_input.toPlainText().strip()
        if not message_text_plain: return # Don't send empty messages

        # Get current formatting from input field
        current_input_format = self.message_input.currentCharFormat()
        display_color = current_input_format.foreground().color()
        display_font = current_input_format.font()

        # Format for display area
        formatted_msg_html = self.format_message(self.my_screen_name, message_text_plain, display_color, display_font)
        self.message_display.append(formatted_msg_html)
        self.message_display.moveCursor(QTextCursor.End) # Scroll to bottom

        # Save to log
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        self._save_message(timestamp, self.my_screen_name, message_text_plain)

        # Emit signal to controller (send the plain text)
        self.message_sent.emit(self.buddy_id, message_text_plain)

        # Play sound
        play_sound_async("send.wav")

        # Clear input field and reset focus
        self.message_input.clear()
        self.message_input.setFocus()

    def receive_message(self, message_text, sender_id=None):
        """Displays and saves an incoming message."""
        # Determine the display name for the sender
        # If sender_id is provided (e.g., for public chat), use it, otherwise use self.display_name (for IMs)
        actual_sender_display = sender_id if sender_id else self.display_name

        # Format for display area (use a default color like blue for others)
        formatted_msg_html = self.format_message(actual_sender_display, message_text, color=Qt.blue, font=self.current_font)
        self.message_display.append(formatted_msg_html)
        self.message_display.moveCursor(QTextCursor.End) # Scroll to bottom

        # Save to log using the actual sender ID
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        self._save_message(timestamp, actual_sender_display, message_text)

        # Alert if window is not active
        if not self.isActiveWindow():
            QApplication.alert(self)

    # --- Event Filter & Close Event ---
    def eventFilter(self, watched_object, event):
        """Handles Enter key press in the message input field."""
        if watched_object is self.message_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if not (event.modifiers() & Qt.ShiftModifier): # Send on Enter unless Shift is held
                    self.send_message()
                    return True # Event handled, don't process further (prevents newline)
        # Pass other events to the base class implementation
        return super().eventFilter(watched_object, event)

    def closeEvent(self, event):
        """Emits closing signal when window is closed."""
        print(f"[ChatWindow] closeEvent for {self.buddy_id}.")
        self.closing.emit(self.buddy_id) # Emit the buddy_id (^all or !nodeid)
        event.accept()


# --- Standalone Test ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    # --- Test Setup ---
    test_log_dir = Path("./test_chat_logs"); test_log_dir.mkdir(exist_ok=True)
    dummy_buddy_id = "test_buddy_!123"; dummy_log_file = test_log_dir / f"{sanitize_filename(dummy_buddy_id)}.log"
    public_log_file = test_log_dir / f"{sanitize_filename(PUBLIC_CHAT_ID)}.log"
    my_name = "MyTestName"

    # Clear previous logs for clean test
    if dummy_log_file.exists(): dummy_log_file.unlink()
    if public_log_file.exists(): public_log_file.unlink()

    # Write some dummy history
    try:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(dummy_log_file, "a", encoding='utf-8') as f:
            f.write(f"[{ts}] {dummy_buddy_id}: Previous IM message 1\n")
            f.write(f"[{ts}] {my_name}: Previous IM message 2\n")
        with open(public_log_file, "a", encoding='utf-8') as f:
            f.write(f"[{ts}] SomeoneElse: Previous public message 1\n")
            f.write(f"[{ts}] {my_name}: Previous public message 2\n")
    except IOError as e: print(f"Could not write dummy logs: {e}")

    # --- Create Windows ---
    # IM Window
    im_chat_win = ChatWindow(my_name, dummy_buddy_id, "Test Buddy Display", auto_save_enabled=True, logs_base_dir=str(test_log_dir))
    im_chat_win.show()

    # Public Chat Window
    public_chat_win = ChatWindow(my_name, PUBLIC_CHAT_ID, "Public Chat", auto_save_enabled=True, logs_base_dir=str(test_log_dir))
    public_chat_win.move(im_chat_win.x() + 50, im_chat_win.y() + 50) # Offset slightly
    public_chat_win.show()

    # --- Simulate Receiving Messages ---
    QTimer.singleShot(1000, lambda: im_chat_win.receive_message("Hello there! This is a test IM."))
    QTimer.singleShot(1500, lambda: public_chat_win.receive_message("This is an incoming public message.", sender_id="AnotherUser!123")) # Provide sender_id

    # --- Execute App ---
    exit_code = app.exec()
    sys.exit(exit_code)
