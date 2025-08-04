import json
import os

from . import config # Import the config module

# Use the absolute path from the config module
SETTINGS_FILE = os.path.join(config.BASE_DIR, "user_settings.json")

def load_settings():
    """
    Loads settings from the JSON file and merges them with defaults.
    """
    # Define default settings
    defaults = {
        "last_ticker_path": "",
        "last_destination_path": "",
        "last_selected_models": ["Gamma", "Term", "Smile", "TV Code"],
        "enable_multi_window": False,
        "enable_scheduler": False
    }
    
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            try:
                user_settings = json.load(f)
                # Merge user settings into defaults, so new keys are added automatically
                defaults.update(user_settings)
            except json.JSONDecodeError:
                pass # Return defaults if file is corrupted
    return defaults

def save_setting(key, value):
    """Saves a specific key-value pair to the settings file."""
    settings = load_settings()
    settings[key] = value
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4)
