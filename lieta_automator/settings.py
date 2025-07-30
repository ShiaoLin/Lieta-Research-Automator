import json
import os

from . import config # Import the config module

# Use the absolute path from the config module
SETTINGS_FILE = os.path.join(config.BASE_DIR, "user_settings.json")

def load_settings():
    """Loads settings from the JSON file."""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}  # Return empty dict if file is corrupted
    return {}

def save_setting(key, value):
    """Saves a specific key-value pair to the settings file."""
    settings = load_settings()
    settings[key] = value
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4)
