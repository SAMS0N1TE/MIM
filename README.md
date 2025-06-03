# MIM - Meshtastic Instant Messenger

An AIM-style chat client using PySide6 that connects via MQTT and/or Meshtastic for decentralized or server-based messaging. Includes optional sound effects and chat logging.
![Screenshot_2025-05-04_14-03-31](https://github.com/user-attachments/assets/c9201331-ee25-4c79-8e14-008c186bd584)
## Features

* Classic AIM-inspired interface (Buddy List, IM windows).
* Connects to Meshtastic devices (via Serial or Network) for direct/broadcast messages.
* Connects to an MQTT broker for server-based chat.
* Configurable settings via UI (node connection, MQTT server, general options).
* Optional sound effects for events (sign on/off, message send/receive, buddy status).
* Optional local chat logging.


## License

This project is licensed under the Creative Commons Attribution-NonCommercial 4.0 International License.

See the [LICENSE](LICENSE) file for details, or visit:
[https://creativecommons.org/licenses/by-nc/4.0/](https://creativecommons.org/licenses/by-nc/4.0/)

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

## Issues

# Troubleshooting Serial Port "Permission Denied" Errors on Linux

If you are using this application on a Linux system to connect to a Meshtastic device (or other hardware) via a USB serial port (e.g., `/dev/ttyACM0`, `/dev/ttyUSB0`), you might encounter a "Permission denied" error when the application tries to access the port. This typically happens when running the application as a regular user. While the application might seem to work if run with `sudo` (using root privileges), this is **not recommended** for regular use due to security risks and potential conflicts.

This "Permission denied" error is a common issue on Linux systems and is related to how the operating system manages access to hardware devices.

## Why This Happens

Serial port device files located in the `/dev/` directory (like `/dev/ttyACM0` or `/dev/ttyUSB0`) are usually owned by the `root` user and belong to a specific system group. Common group names for serial devices include `dialout`, `tty`, or `uucp`, depending on your Linux distribution. By default, regular users are not members of this group and therefore don't have the necessary permissions to read from or write to the serial port.

## How to Fix It (Step-by-Step)

The correct way to resolve this is to add your user account to the group that has permissions for the serial device. Follow these steps:

1.  **Identify Your Serial Device Name:**
    * Connect your USB serial device (e.g., LoRa device, Meshtastic node) to your computer.
    * Open a terminal and find its device name. Common names include `/dev/ttyACM0` or `/dev/ttyUSB0`. You can identify it by:
        * Running `dmesg | tail` immediately after plugging in the device. Look for messages at the end of the output that indicate the assigned device name.
        * Listing potential devices: `ls /dev/ttyACM* /dev/ttyUSB*`

2.  **Find the Device's Group Ownership:**
    * Once you know the device name (e.g., `/dev/ttyACM0`), use the `ls -l` command to see its permissions and, importantly, its group:
        ```bash
        ls -l /dev/your_device_name
        ```
        *(Replace `/dev/your_device_name` with the actual device path, for example: `ls -l /dev/ttyACM0`)*
    * The output will look something like this:
        `crw-rw---- 1 root dialout 166, 0 Jun  3 10:40 /dev/ttyACM0`
        In this example, the group is `dialout`. Note down this group name, as you'll need it for the next step.

3.  **Add Your User to the Group:**
    * Now, add your current user to the group you identified. If the group was `dialout`, for instance, the command is:
        ```bash
        sudo usermod -a -G dialout $USER
        ```
        *(Replace `dialout` if your device belongs to a different group. `$USER` is an environment variable that automatically resolves to your current username.)*
    * You will be prompted for your password because this command requires administrative (`sudo`) privileges to modify user accounts.

4.  **Apply Group Changes (CRUCIAL STEP!):**
    * For the new group membership to take full effect across your system and for your user session, you **must log out of your current desktop session and then log back in.**
    * Alternatively, a full reboot of your computer will also achieve this.
    * *Simply closing and reopening the terminal is often not sufficient for these changes to apply to your graphical session and new processes.*

5.  **Verify Group Membership (Optional):**
    * After logging back in, you can open a terminal and verify that your user is now part of the group:
        ```bash
        groups $USER
        ```
        You should see the group (e.g., `dialout`) in the list of groups your user belongs to.

6.  **Configure the Correct Port in This Application:**
    * Launch the MIM application.
    * Navigate to the "Settings / Setup" window (this can usually be accessed via a "Setup" button on the login screen or from a "File" > "Settings" menu option within the application).
    * Under the "Meshtastic Node" connection settings (or similar section for your serial device), ensure the "Connection" type is set to "Serial".
    * In the "Details" field for the serial connection, enter the correct device path you identified in Step 1 (e.g., `/dev/ttyACM0`).
    * Save the application settings if necessary.

7.  **Run the Application:**
    * Try connecting to your serial device through the application again (without using `sudo`). The "Permission denied" error should now be resolved, and the application should be able to access the port.

### Important Considerations:

* **Avoid `sudo` for Running the Application:** Do not run the main application with `sudo` as a regular workaround for permission issues. Fixing the group membership as described above is the correct, more secure, and stable method.
* **`udev` Rules (Advanced Users):** For users who require more permanent or customized device permissions, or for situations where device names might change (e.g., `/dev/ttyUSB0` sometimes becomes `/dev/ttyUSB1`), creating `udev` rules is a more robust, albeit more advanced, solution. This involves creating a rule file in `/etc/udev/rules.d/` to set specific permissions or create a persistent symbolic link for your device. However, for most users, adding your user to the appropriate group is sufficient and simpler.

