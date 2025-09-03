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
        "schedule_time": "17:00",  # Combined time
        "schedule_type": "DAILY"   # New schedule type
    }
    
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                user_settings = json.load(f)
                
                # Backwards compatibility for old settings format
                if "schedule_time_hour" in user_settings:
                    user_settings["schedule_time"] = f"{user_settings.get('schedule_time_hour', '17')}:{user_settings.get('schedule_time_minute', '00')}"
                    del user_settings["schedule_time_hour"]
                    if "schedule_time_minute" in user_settings:
                        del user_settings["schedule_time_minute"]

                # Merge user settings into defaults
                defaults.update(user_settings)
        except (json.JSONDecodeError, IOError):
            pass 
    return defaults

def save_settings(settings_data):
    """Saves the entire settings dictionary to the JSON file."""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings_data, f, indent=4, ensure_ascii=False)
