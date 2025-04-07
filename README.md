# MIM - Meshtastic Instant Messenger

An AIM-style chat client using PySide6 that connects via MQTT and/or Meshtastic for decentralized or server-based messaging. Includes optional sound effects and chat logging.

## Features

* Classic AIM-inspired interface (Buddy List, IM windows).
* Connects to Meshtastic devices (via Serial or Network) for direct/broadcast messages.
* Connects to an MQTT broker for server-based chat.
* Configurable settings via UI (node connection, MQTT server, general options).
* Optional sound effects for events (sign on/off, message send/receive, buddy status).
* Optional local chat logging.

## Installation

These instructions will guide you through setting up the project on Windows or Linux.

**Prerequisites:**

* **Python:** Ensure you have Python 3 (version 3.8 or newer recommended) installed. You can download it from [python.org](https://www.python.org/). Verify by opening a terminal or command prompt and typing `python --version`.
* **Git:** Ensure you have Git installed. You can download it from [git-scm.com](https://git-scm.com/).

**Steps:**

1.  **Clone the repository:**
    Open your terminal (Linux) or Command Prompt/PowerShell (Windows) and run:
    ```bash
    git clone https://github.com/SAMS0N1TE/MIM.git
    cd MIM
    ```

2.  **Create and activate a virtual environment (Recommended, but not required):**
    This keeps the project's dependencies isolated. Run these commands inside the project directory (`MIM`):
    ```bash
    python -m venv venv
    ```
    Now, activate the environment:
    * **Windows (Command Prompt/PowerShell):**
        ```bash
        .\venv\Scripts\activate
        ```
    * **Linux / macOS (bash/zsh):**
        ```bash
        source venv/bin/activate
        ```
    You should see `(venv)` appear at the beginning of your terminal prompt.

3.  **Install dependencies:**
    With the virtual environment activated, install the required Python packages using the `requirements.txt` file:
    ```bash
    pip install -r requirements.txt
    ```
    This will install [PySide6](https://pypi.org/project/PySide6/), [paho-mqtt](https://pypi.org/project/paho-mqtt/), [meshtastic](https://github.com/meshtastic/python), [pyserial](https://github.com/pyserial/pyserial), [pygame](https://github.com/pygame/pygame), and [pypubsub](https://github.com/Humbedooh/pypubsub).
    
## Usage
 
1.  Make sure your dependencies are installed and settings are configured.
2.  Run the main script:
    ```bash
    python main.py
    ```
3.  Enter your configured Screen Name in the Sign On window. Enter the MQTT password if required by your broker.
4.  Click **Sign On** (or the Sign On icon).
5.  The Buddy List window should appear. Meshtastic nodes should populate automatically after a short delay if configured and connected. MQTT buddies would need manual adding or presence implementation (not currently included).
6.  Double-click a buddy or select one and click **IM** to open a chat window.

## License

This project is licensed under the Creative Commons Attribution-NonCommercial 4.0 International License.

See the [LICENSE](LICENSE) file for details, or visit:
[https://creativecommons.org/licenses/by-nc/4.0/](https://creativecommons.org/licenses/by-nc/4.0/)
