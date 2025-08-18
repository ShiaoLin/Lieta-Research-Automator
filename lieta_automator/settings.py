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
        "schedule_enabled": False,
        "schedule_time_hour": "17", # Default hour
        "schedule_time_minute": "00"  # Default minute
    }
    
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
                # Merge user settings into defaults, so new keys are added automatically
                defaults.update(user_settings)
        except (json.JSONDecodeError, IOError):
            # If file is corrupted or unreadable, return defaults
            pass 
    return defaults

def save_settings(settings_data):
    """Saves the entire settings dictionary to the JSON file."""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings_data, f, indent=4, ensure_ascii=False)
