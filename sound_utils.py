# sound_utils.py
import os
import sys
import threading
import time

SOUNDS_ENABLED = True

try:
    import pygame
    pygame.mixer.init()
    sound_library_available = True
    print("Pygame mixer initialized successfully.")
except ImportError:
    print("WARNING: pygame library not found. Sound features will be disabled.")
    print("Install it using: pip install pygame")
    pygame = None
    sound_library_available = False
except pygame.error as pg_err:
    print(f"WARNING: Pygame mixer could not be initialized. Sound features may be disabled.")
    print(f"Error: {pg_err}")
    pygame = None
    sound_library_available = False

last_buddy_sound_time = 0
BUDDY_SOUND_THROTTLE_SECONDS = 1.5

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def set_sounds_enabled(enabled: bool):
    global SOUNDS_ENABLED
    SOUNDS_ENABLED = enabled
    print(f"[Sound Utils] Sounds enabled state set to: {SOUNDS_ENABLED}")

def play_sound_async(sound_filename):
    global last_buddy_sound_time
    global SOUNDS_ENABLED

    if not SOUNDS_ENABLED:
        return

    if not sound_library_available:
        return

    try:
        sound_path = get_resource_path(os.path.join("resources", "sounds", sound_filename))
        if not os.path.exists(sound_path):
            print(f"Sound file not found: {sound_path}")
            return

        if sound_filename == "buddyin.wav":
            current_time = time.time()
            if current_time - last_buddy_sound_time < BUDDY_SOUND_THROTTLE_SECONDS:
                return
            else:
                last_buddy_sound_time = current_time

        def play_it():
            try:
                sound = pygame.mixer.Sound(sound_path)
                sound.play()
            except Exception as e:
                print(f"Error during pygame sound playback '{sound_filename}': {e}")

        sound_thread = threading.Thread(target=play_it, daemon=True)
        sound_thread.start()

    except Exception as e:
        print(f"Error preparing sound '{sound_filename}': {e}")
