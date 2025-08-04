import os
import sys

# --- Path Configuration (Handles PyInstaller) ---
# Determine if the application is running as a bundled executable
if getattr(sys, 'frozen', False):
    # If bundled, the base directory is the directory of the executable
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # If running as a script, the base directory is the project root
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# --- Chrome and Selenium Settings ---
CHROME_EXECUTABLE_PATH = None # Set to a specific path if auto-detection fails
# A list of ports to allow for concurrent Chrome instances.
REMOTE_DEBUGGING_PORTS = [9222, 9223, 9224, 9225]
SELENIUM_TIMEOUT = 10 # seconds

def get_chrome_user_data_dir(port: int) -> str:
    """
    Generates a unique user data directory path for a given Chrome debugging port.
    - Port 9222 (the default) uses 'automation_profile'.
    - Subsequent ports (9223, 9224, ...) use 'automation_profile_2', '_3', etc.
    """
    if port == 9222:
        profile_name = "automation_profile"
    else:
        # Calculate the suffix based on the port number offset from the base port
        suffix = port - 9222 + 1
        profile_name = f"automation_profile_{suffix}"
        
    return os.path.join(BASE_DIR, profile_name)


# --- Application Settings ---
LIETA_PLATFORM_URL = "https://www.lietaresearch.com/"
LIETA_AUTOMATION_URL = "https://www.lietaresearch.com/platform"
# Define main temp download dir
TEMP_DOWNLOAD_DIR_NAME = os.path.join(BASE_DIR, "temp_downloads")

def get_temp_download_path_for_port(port: int) -> str:
    """
    Generates a unique temporary download directory for a given port to avoid
    race conditions in multi-window mode.
    """
    return os.path.join(TEMP_DOWNLOAD_DIR_NAME, str(port))

