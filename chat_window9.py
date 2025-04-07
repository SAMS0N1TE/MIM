# chat_window.py
import sys
import os
# **** Added for timestamping and path validation ****
import datetime
import re
from pathlib import Path
# *************************************************
import sys
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit,
    QPushButton, QToolBar, QStatusBar, QSizePolicy, QApplication, QColorDialog,
    # Import QInputDialog
    QFontDialog, QMenuBar, QMessageBox, QInputDialog
)
from PySide6.QtGui import (
    QAction, QIcon, QTextCursor, QColor, QFont, QKeySequence, QPixmap,
    QFontDatabase, QTextCharFormat
)
# Import Qt for Qt.blue, Qt.black etc.
from PySide6.QtCore import Qt, Signal, QTimer, QEvent, QSize, QStandardPaths # Added QStandardPaths

# --- Helper function ---
def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # sys._MEIPASS attribute fails if not running in PyInstaller bundle
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
# --- Function to sanitize filenames ---
def sanitize_filename(filename):
    """Removes or replaces invalid characters for filenames."""
    # Remove characters that are explicitly invalid on Windows/Linux/Mac
    # Replace others like '!' or ':' which might be problematic
    sanitized = re.sub(r'[\\/*?:"<>|!]', '_', filename)
    # Remove leading/trailing dots or spaces
    sanitized = sanitized.strip('. ')
    # Handle reserved names if necessary (e.g., CON, PRN) - less likely for node IDs
    if not sanitized: # Handle empty filename case
        sanitized = "_empty_"
    return sanitized

class ChatWindow(QMainWindow):
    """
    Represents the individual chat window for an IM conversation,
    mimicking the classic AIM style.
    """
    closing = Signal(str) # Pass buddy ID
    message_sent = Signal(str, str) # Pass recipient_id, message_text
    # **** Updated constructor ****
    def __init__(self, my_screen_name, buddy_id, auto_save_enabled=False, logs_base_dir=None):
        super().__init__()
        self.my_screen_name = my_screen_name
        self.buddy_id = buddy_id
        # **** Store auto-save setting ****
        self.auto_save_enabled = auto_save_enabled
        self.log_file_path = None

        # **** Determine Log File Path ****
        if self.auto_save_enabled and logs_base_dir and self.buddy_id:
            try:
                # Ensure logs base directory exists
                log_dir = Path(logs_base_dir)
                log_dir.mkdir(parents=True, exist_ok=True)
                # Sanitize buddy ID for use as filename
                safe_buddy_id = sanitize_filename(self.buddy_id)
                self.log_file_path = log_dir / f"{safe_buddy_id}.log"
                print(f"[ChatWindow] Logging enabled for {self.buddy_id} to: {self.log_file_path}")
            except Exception as e:
                print(f"[ChatWindow] Error setting up log path for {self.buddy_id}: {e}")
                self.auto_save_enabled = False # Disable saving if path setup fails
        else:
            print(f"[ChatWindow] Logging disabled for {self.buddy_id}. AutoSave={auto_save_enabled}, Dir={logs_base_dir}")

        self.setWindowTitle(f"IM with {self.buddy_id}")
        self.setMinimumSize(400, 350)

        # --- Central Widget and Layout ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(3)

        # --- Message Display Area ---
        self.message_display = QTextEdit()
        self.message_display.setReadOnly(True)
        main_layout.addWidget(self.message_display, 1)

        # --- Formatting Toolbar ---
        self.formatting_toolbar = QToolBar("Formatting")
        self.formatting_toolbar.setStyleSheet("QToolBar { border: none; padding: 1px; }")

        # --- Message Input Area & Send Button ---
        input_layout = QHBoxLayout()
        input_layout.setSpacing(3)

        self.message_input = QTextEdit()
        self.message_input.setFixedHeight(60)
        self.message_input.installEventFilter(self)

        self.send_button = QPushButton()
        send_icon_path = get_resource_path("resources/icons/send_icon.png")
        send_icon = QIcon(send_icon_path)
        if not send_icon.isNull():
            self.send_button.setIcon(send_icon); self.send_button.setIconSize(QSize(24, 24))
            self.send_button.setIconSize(QSize(24, 24))
            self.send_button.setIcon(send_icon); self.send_button.setIconSize(QSize(24, 24))
            # Minimal padding for icon button look
            self.send_button.setStyleSheet("QPushButton { padding: 2px; }")
        else:
            # Fallback if icon doesn't load
            print(f"Warning: Could not load send icon from {send_icon_path}")
            self.send_button.setText("Send")

        # Add input field and send button to their horizontal layout
        input_layout.addWidget(self.message_input, 1) # Input field stretches
        input_layout.addWidget(self.send_button) # Send button fixed size

        # --- Add elements to main vertical layout in order ---
        main_layout.addWidget(self.formatting_toolbar) # Toolbar below display
        main_layout.addLayout(input_layout) # Input layout below toolbar

        # --- Create Menu Bar (needs self.message_input to exist) ---
        self._create_menu_bar()

        # --- Create Actions & Populate Toolbar (need actions before population) ---
        self._create_format_actions() # Define self.bold_action etc.
        self._populate_formatting_toolbar(self.formatting_toolbar) # Add actions to toolbar

        # --- Connect Signals (widgets and actions must exist) ---
        self.message_input.currentCharFormatChanged.connect(self.update_format_button_states)
        self.send_button.clicked.connect(self.send_message)

        # --- Status Bar ---
        self.statusBar().showMessage(f"Chatting with {self.buddy_id}")

        # --- Set Initial Formatting (needs actions to exist for button updates) ---
        self._set_default_formatting()
        # **** Load History ****
        self._load_history()


    def _set_default_formatting(self):
        """Sets the initial font and color for the input field and updates buttons."""
        default_font_family = "Helvetica"
        default_font_size = 10
        available_families = QFontDatabase.families() # Check all available families

        # Set self.current_font based on availability
        if default_font_family not in available_families:
             print(f"Warning: '{default_font_family}' font not found, using system default.")
             self.current_font = QFont() # Get default system font
             self.current_font.setPointSize(default_font_size)
        else:
             self.current_font = QFont(default_font_family, default_font_size)

        self.current_color = Qt.black # Default text color

        # Apply initial format to the input widget
        self.message_input.setCurrentFont(self.current_font)
        self.message_input.setTextColor(self.current_color)

        # Update formatting button states (B/I/U) based on this initial format
        if hasattr(self, 'bold_action'): # Check actions exist
            self.update_format_button_states(self.message_input.currentCharFormat())
        else: # Fallback timer if initialization order was wrong
            QTimer.singleShot(0, lambda: self.update_format_button_states(self.message_input.currentCharFormat()))


    def _create_menu_bar(self):
        """Creates the main menu bar with File, Edit, View, People."""
        # Assumes self.message_input exists for Edit menu connections
        menu_bar = self.menuBar()

        # --- File Menu ---
        file_menu = menu_bar.addMenu("&File")
        save_action = QAction("&Save Conversation...", self)
        save_action.setEnabled(False) # Placeholder
        close_action = QAction("&Close", self)
        close_action.setShortcut(QKeySequence.Close)
        close_action.triggered.connect(self.close) # Connect to window close
        file_menu.addAction(save_action)
        edit_menu = menu_bar.addMenu("&Edit"); undo_action = QAction("&Undo", self); undo_action.setShortcut(QKeySequence.Undo); undo_action.triggered.connect(self.message_input.undo)
        file_menu.addSeparator()
        file_menu.addAction(close_action)

        # --- Edit Menu ---
        edit_menu = menu_bar.addMenu("&Edit")
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.Undo)
        undo_action.triggered.connect(self.message_input.undo) # Connect to QTextEdit slot

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.Redo)
        redo_action.triggered.connect(self.message_input.redo) # Connect to QTextEdit slot

        cut_action = QAction("Cu&t", self)
        cut_action.setShortcut(QKeySequence.Cut)
        cut_action.triggered.connect(self.message_input.cut) # Connect to QTextEdit slot

        copy_action = QAction("&Copy", self)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(self.message_input.copy) # Connect to QTextEdit slot

        paste_action = QAction("&Paste", self)
        paste_action.setShortcut(QKeySequence.Paste)
        paste_action.triggered.connect(self.message_input.paste) # Connect to QTextEdit slot

        select_all_action = QAction("Select &All", self)
        select_all_action.setShortcut(QKeySequence.SelectAll)
        select_all_action.triggered.connect(self.message_input.selectAll) # Connect to QTextEdit slot
        edit_menu.addAction(undo_action); edit_menu.addAction(redo_action); edit_menu.addSeparator(); edit_menu.addAction(cut_action); edit_menu.addAction(copy_action); edit_menu.addAction(paste_action); edit_menu.addSeparator(); edit_menu.addAction(select_all_action)
        # Add actions to Edit menu
        edit_menu.addAction(undo_action)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(cut_action)
        edit_menu.addAction(copy_action)
        edit_menu.addAction(paste_action)
        edit_menu.addSeparator()
        edit_menu.addAction(select_all_action)

        # --- View Menu (Placeholders) ---
        view_menu = menu_bar.addMenu("&View")
        away_msg_action = QAction("Away &Message...", self)
        away_msg_action.setEnabled(False) # Placeholder
        view_menu.addAction(away_msg_action)

        # --- People Menu (Placeholders) ---
        people_menu = menu_bar.addMenu("&People")
        get_info_action = QAction("&Get Info...", self)
        get_info_action.setEnabled(False) # Placeholder
        block_action = QAction("&Block...", self)
        block_action.setEnabled(False) # Placeholder
        warn_action = QAction("&Warn...", self)
        warn_action.setEnabled(False) # Placeholder
        # Add actions to People menu
        people_menu.addAction(get_info_action)
        people_menu.addSeparator()
        people_menu.addAction(block_action)
        people_menu.addAction(warn_action)


    def _create_format_actions(self):
        """Creates the QActions for formatting toolbar and stores them on self."""
        icon_path_base = get_resource_path("resources/icons/")

        # --- Font Action ---
        font_icon = QIcon(os.path.join(icon_path_base, "font.png"))
        self.font_action = QAction(font_icon, "&Font...", self)
        self.font_action.setToolTip("Change Font")
        self.font_action.triggered.connect(self.select_font)

        # --- Color Action ---
        color_icon = QIcon(os.path.join(icon_path_base, "color.png"))
        self.color_action = QAction(color_icon, "&Color...", self)
        self.color_action.setToolTip("Change Text Color")
        self.color_action.triggered.connect(self.select_color)

        # --- Bold Action ---
        bold_icon = QIcon(os.path.join(icon_path_base, "bold.png"))
        self.bold_action = QAction(bold_icon, "&Bold", self)
        self.bold_action.setShortcut(QKeySequence.Bold)
        self.bold_action.setCheckable(True) # Allows toggle state
        self.bold_action.setToolTip("Bold Text (Ctrl+B)")
        self.bold_action.triggered.connect(self.toggle_bold)

        # --- Italic Action ---
        italic_icon = QIcon(os.path.join(icon_path_base, "italic.png"))
        self.italic_action = QAction(italic_icon, "&Italic", self)
        self.italic_action.setShortcut(QKeySequence.Italic)
        self.italic_action.setCheckable(True)
        self.italic_action.setToolTip("Italic Text (Ctrl+I)")
        self.italic_action.triggered.connect(self.toggle_italic)

        # --- Underline Action ---
        underline_icon = QIcon(os.path.join(icon_path_base, "underline.png"))
        self.underline_action = QAction(underline_icon, "&Underline", self)
        self.underline_action.setShortcut(QKeySequence.Underline)
        self.underline_action.setCheckable(True)
        self.underline_action.setToolTip("Underline Text (Ctrl+U)")
        self.underline_action.triggered.connect(self.toggle_underline)

        # --- Link Action ---
        link_icon = QIcon(os.path.join(icon_path_base, "link.png"))
        self.link_action = QAction(link_icon, "&Link...", self)
        self.link_action.setToolTip("Insert Hyperlink")
        self.link_action.triggered.connect(self.insert_link_placeholder)

        # --- Smiley Action ---
        smiley_icon = QIcon(os.path.join(icon_path_base, "smiley.png"))
        self.smiley_action = QAction(smiley_icon, "&Smiley...", self)
        self.smiley_action.setToolTip("Insert Smiley")
        self.smiley_action.triggered.connect(self.insert_smiley_placeholder)


    def _populate_formatting_toolbar(self, toolbar):
        """Populates the given toolbar with formatting actions created earlier."""
        # Add actions (assumes they exist on self)
        toolbar.addAction(self.font_action)
        toolbar.addAction(self.color_action)
        toolbar.addSeparator() # Separator between font/color and B/I/U
        toolbar.addAction(self.bold_action)
        toolbar.addAction(self.italic_action)
        toolbar.addAction(self.underline_action)
        toolbar.addSeparator() # Separator between B/I/U and link/smiley
        toolbar.addAction(self.link_action)
        toolbar.addAction(self.smiley_action)

        # Optional: Check icon loading here
        # Could iterate through toolbar.actions() and check action.icon().isNull()

    # --- Formatting Action Handlers ---

    def select_font(self):
        """Opens font dialog and applies selected font to selection/cursor."""
        ok, font = QFontDialog.getFont(self.message_input.currentFont(), self, "Select Font")
        if ok:
            fmt = QTextCharFormat()
            fmt.setFont(font)
            self.merge_format_on_selection(fmt)

    def select_color(self):
        """Opens color dialog and applies selected color to selection/cursor."""
        current_color = self.message_input.textColor() # Get color at cursor
        color = QColorDialog.getColor(current_color, self, "Select Color")
        if color.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            self.merge_format_on_selection(fmt)

    def toggle_bold(self):
        """Toggles bold formatting based on action's checked state."""
        self.set_selected_text_format("bold", self.bold_action.isChecked())

    def toggle_italic(self):
        """Toggles italic formatting based on action's checked state."""
        self.set_selected_text_format("italic", self.italic_action.isChecked())

    def toggle_underline(self):
        """Toggles underline formatting based on action's checked state."""
        self.set_selected_text_format("underline", self.underline_action.isChecked())

    def insert_link_placeholder(self):
        """Gets URL via dialog and inserts a basic hyperlink, then resets format."""
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
                 # Move cursor after selection to reset format there
                 cursor.clearSelection()
                 cursor.movePosition(QTextCursor.EndOfBlock if cursor.atBlockEnd() else QTextCursor.NextCharacter) # Move cursor after formatted text
            else:
                 cursor.insertText(text, link_fmt) # Insert URL string with link format
                 # Cursor is now after inserted text

            # --- Reset format for subsequent typing ---
            # Create a new default format object
            default_fmt = QTextCharFormat()
            # Set its properties based on the desired default typing style
            default_fmt.setAnchor(False)
            default_fmt.setFontUnderline(False)
            # Use the 'current_color' and 'current_font' we stored as defaults
            default_fmt.setForeground(self.current_color) # Reset to user's default color
            default_fmt.setFont(self.current_font) # Reset to user's default font (weight/style included)

            # Apply this default format to the input widget's current format setting
            self.message_input.setCurrentCharFormat(default_fmt)

            self.message_input.setFocus()

    def insert_smiley_placeholder(self):
        """Inserts a basic smiley text."""
        cursor = self.message_input.textCursor()
        # **** Use isNull() check ****
        if cursor.isNull(): return # Check if cursor is valid
        cursor.insertText(" :) ") # Insert smiley text
        self.message_input.setFocus() # Return focus to input

    # --- Formatting Helper Methods ---

    def set_selected_text_format(self, property_str, value):
        """Applies a specific font property (bold, italic, underline) using string identifiers."""
        fmt = QTextCharFormat()
        # Check property string and set corresponding format attribute
        if property_str == "bold":
            fmt.setFontWeight(QFont.Bold if value else QFont.Normal)
        elif property_str == "italic":
            fmt.setFontItalic(value)
        elif property_str == "underline":
            fmt.setFontUnderline(value)
        else:
            print(f"Warning: Unknown format property '{property_str}'")
            return # Exit if property is not recognized

        self.merge_format_on_selection(fmt) # Apply the format

    def merge_format_on_selection(self, format_to_merge):
        """Merges the given format with the current selection or cursor format."""
        cursor = self.message_input.textCursor()
        # **** Use isNull() check ****
        if cursor.isNull(): return # Check if cursor is valid

        # mergeCharFormat applies to selection if one exists,
        # otherwise it sets the format for subsequent typing at the cursor position.
        cursor.mergeCharFormat(format_to_merge)
        # Also merge with the document's current format to ensure persistence
        self.message_input.mergeCurrentCharFormat(format_to_merge)
        self.message_input.setFocus() # Keep focus on input field

    def update_format_button_states(self, current_format):
        """Updates the checked state of B/I/U buttons based on cursor/selection format."""
        # Get formatting properties from the provided QTextCharFormat
        is_bold = current_format.fontWeight() == QFont.Bold
        is_italic = current_format.fontItalic()
        is_underline = current_format.fontUnderline()

        # Update checkable actions, block signals to prevent triggering toggle methods
        if hasattr(self, 'bold_action'):
            self.bold_action.blockSignals(True); self.bold_action.setChecked(is_bold); self.bold_action.blockSignals(False)
        if hasattr(self, 'italic_action'):
            self.italic_action.blockSignals(True); self.italic_action.setChecked(is_italic); self.italic_action.blockSignals(False)
        if hasattr(self, 'underline_action'):
            self.underline_action.blockSignals(True); self.underline_action.setChecked(is_underline); self.underline_action.blockSignals(False)

    # --- Message Handling ---

    def format_message(self, who, message_text, color=None, font=None):
        """Formats a message line with HTML for display. (Handles basic font properties)"""
        # Determine base font
        display_font = font if font else QFont("Helvetica", 10)
        if "Helvetica" not in QFontDatabase.families(): display_font = QFont("Arial", 10)

        # Determine color (use current input text color for self if not specified)
        if color is None:
            display_color = Qt.red if who != self.my_screen_name else self.message_input.textColor()
        else:
            display_color = color

        # Extract properties for CSS
        color_hex = QColor(display_color).name()
        font_family = display_font.family()
        font_size = display_font.pointSize(); size_unit = "pt"
        font_weight = 'bold' if display_font.bold() else 'normal'
        font_style = 'italic' if display_font.italic() else 'normal'
        text_decoration = 'underline' if display_font.underline() else 'none'

        # Build style string
        style = f"font-family: '{font_family}'; font-size: {font_size}{size_unit}; color: {color_hex};"
        style += f" font-weight: {font_weight}; font-style: {font_style}; text-decoration: {text_decoration};"

        # HTML structure
        formatted = f'<span style="{style}">'
        safe_who = who.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        formatted += f'<b>{safe_who}:</b> ' # Nickname always bold

        # Escape message text and handle newlines
        # Assumes message_text does not contain intended HTML formatting
        escaped_message = message_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        escaped_message = escaped_message.replace('\n', '<br>')
        formatted += escaped_message
        formatted += '</span>'
        return formatted

    def send_message(self):
        """Formats message based on current input format and emits plain text."""
        message_text_plain = self.message_input.toPlainText().strip()
        if not message_text_plain:
            return # Don't send empty messages

        # Get current format at cursor for local display formatting
        current_input_format = self.message_input.currentCharFormat()
        display_color = current_input_format.foreground().color()
        display_font = current_input_format.font()

        # Format for *local* display
        formatted_msg = self.format_message(self.my_screen_name, message_text_plain, display_color, display_font)
        self.message_display.append(formatted_msg)

        # Emit PLAIN TEXT signal - formatting is NOT sent over MQTT yet
        self.message_sent.emit(self.buddy_id, message_text_plain)

        self.message_input.clear()
        self.message_input.setFocus()

    def receive_message(self, message_text):
        """Displays received message (assumes plain text)."""
        # Formats incoming message with default settings (e.g., red text)
        formatted_msg = self.format_message(self.buddy_id, message_text)
        self.message_display.append(formatted_msg)
        self.message_display.moveCursor(QTextCursor.End) # Auto-scroll

        # Alert user if window isn't active
        if not self.isActiveWindow():
             QApplication.alert(self)

    # --- Event Filter & Close Event ---

    def eventFilter(self, obj, event):
        """Handles Enter key press in the input field for sending messages."""
        if obj is self.message_input and event.type() == QEvent.Type.KeyPress:
            # Check if Enter/Return key was pressed
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                # Send message only if Shift modifier is NOT pressed
                if not (event.modifiers() & Qt.ShiftModifier):
                    self.send_message()
                    return True # Consume the event, preventing newline insertion
        # Pass all other events to the base class implementation
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        """Emits closing signal when window is closed by user."""
        print(f"Chat window for {self.buddy_id} is closing.")
        self.closing.emit(self.buddy_id) # Notify controller/buddy list
        event.accept() # Allow the window to close


# --- Standalone Test ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    # Load font(s) for standalone testing
    QFontDatabase.addApplicationFont(get_resource_path("helvetica/Helvetica.ttf"))
    # Create and show the chat window
    chat_win = ChatWindow("MyName", "Buddy123")
    chat_win.show()
    # Simulate receiving a message after 2 seconds
    QTimer.singleShot(2000, lambda: chat_win.receive_message("Hello there! This is a test message."))
    # Start the Qt event loop
    sys.exit(app.exec())