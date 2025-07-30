# run.py
from lieta_automator import main
import sys

if __name__ == "__main__":
    try:
        main.main()
    except Exception as e:
        # This provides a top-level catch for any unexpected errors in the bundled app
        print(f"An unexpected error occurred: {e}")
        # In a real app, this might be logged to a file.
        sys.exit(1)
