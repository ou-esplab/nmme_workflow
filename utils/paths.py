from pathlib import Path

def ensure_dir(path):
    """
    Ensure that a directory exists.
    Accepts str or Path.
    """
    Path(path).mkdir(parents=True, exist_ok=True)
