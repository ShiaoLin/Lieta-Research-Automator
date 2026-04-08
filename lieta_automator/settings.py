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
    }

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
                # Strip out legacy scheduler keys if present
                for key in ("schedule_enabled", "schedule_time", "schedule_type",
                            "schedule_time_hour", "schedule_time_minute", "enable_scheduler"):
                    user_settings.pop(key, None)
                defaults.update(user_settings)
        except (json.JSONDecodeError, IOError):
            pass
    return defaults

def save_settings(settings_data):
    """Saves the entire settings dictionary to the JSON file."""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings_data, f, indent=4, ensure_ascii=False)
