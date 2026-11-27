import os, sqlite3
from pathlib import Path
from datetime import datetime, timezone
def main():
    source=Path(os.environ.get("LOG_POWER_DB","log_power_data/points.sqlite3"))
    if not source.is_file(): raise FileNotFoundError(source)
    target=source.parent/"backups"
    target.mkdir(mode=0o700,exist_ok=True)
    dest=target/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+".sqlite3")
    with sqlite3.connect(source) as src, sqlite3.connect(dest) as dst:
        src.backup(dst)
    dest.chmod(0o600)
if __name__=="__main__": main()
