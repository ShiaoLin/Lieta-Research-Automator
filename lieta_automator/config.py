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
REMOTE_DEBUGGING_PORT = 9222
# Define paths as absolute paths based on BASE_DIR
CHROME_USER_DATA_DIR = os.path.join(BASE_DIR, "automation_profile")
SELENIUM_TIMEOUT = 10 # seconds

# --- Application Settings ---
LIETA_PLATFORM_URL = "https://www.lietaresearch.com/"
LIETA_AUTOMATION_URL = "https://www.lietaresearch.com/platform"
# Define temp download dir as an absolute path
TEMP_DOWNLOAD_DIR_NAME = os.path.join(BASE_DIR, "temp_downloads")
