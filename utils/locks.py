import os

def make_lock(lockfile: str):
    if os.path.exists(lockfile):
        raise RuntimeError("Lock exists: pipeline already running")
    with open(lockfile, "w") as f:
        f.write(str(os.getpid()))

def remove_lock(lockfile: str):
    if os.path.exists(lockfile):
        os.remove(lockfile)
