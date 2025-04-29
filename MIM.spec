# -*- mode: python ; coding: utf-8 -*-

# Import necessary PyInstaller utilities
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Analysis section: defines the main script and finds dependencies
a = Analysis(
    ['main.py'], # Your main script
    pathex=[], # Add paths to search for imports if needed
    binaries=[], # Add non-python libraries (.dll, .so) if needed
    datas=[
         # --- Include the entire 'resources' directory ---
         ('resources', 'resources'),
         # --- Include the entire 'certs' directory ---
         ('certs', 'certs')
         # Add other data files or directories here if necessary
         # Format: ('source/path/on/disk', 'destination/path/in/exe')
    ],
    hiddenimports=[
        # --- Modules PyInstaller might miss ---
        'pygame', # For sound_utils
        'serial.tools.list_ports', # For settings_window serial detection
        'pubsub', # For meshtastic pubsub
        'paho', # Base package for paho-mqtt
        'paho.mqtt.client',
        'paho.mqtt.publish',
        'meshtastic.serial_interface', # Explicitly include interfaces
        'meshtastic.tcp_interface',
        'ssl', # Often needed implicitly
        # Add other potential hidden imports if errors occur
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Explicitly collect Pygame data files ---
# This line helps bundle necessary Pygame DLLs and resources
datas = a.datas # Start with datas from Analysis
# Ensure each item from collect_data_files has the 'DATA' typecode
for item in collect_data_files('pygame'):
    if len(item) == 2: # If typecode is missing
        datas.append((item[0], item[1], 'DATA')) # Add 'DATA' typecode
    else:
        datas.append(item) # Assume it's already correct
a.datas = datas # Update Analysis datas with Pygame files


# PY Z section: bundles Python libraries
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# EXE section: creates the executable
exe = EXE(
    pyz,
    a.scripts,
    # Pass binaries from Analysis
    a.binaries,
    # Pass zipfiles from Analysis
    a.zipfiles,
    # Pass datas from Analysis (now includes resources, certs, pygame)
    a.datas,
    [],
    name='MIM', # Name of the final executable
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True, # Use UPX for compression if installed (optional)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # Set to False for GUI applications (no console window)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # --- Specify path to your .ico file based on tree output ---
    icon='resources/icons/app_icon.ico'
)

# --- ADDED BACK: COLLECT section (for one-folder builds) ---
# This gathers the EXE and all dependencies into a folder.
coll = COLLECT(
    exe,
    a.binaries,
    # Pass zipfiles from Analysis
    a.zipfiles,
    # Pass datas from Analysis (now includes resources, certs, pygame)
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MIM' # Name of the output folder
)
