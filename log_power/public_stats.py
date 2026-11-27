"""Read-only aggregate export; never publish IDs, questions, or transaction details."""
import json, os, sqlite3, time
from pathlib import Path
from datetime import datetime, timedelta, timezone

def snapshot(path):
    path=Path(path)
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as c:
        c.execute("BEGIN")
        users,balance=c.execute("SELECT COUNT(*),COALESCE(SUM(balance),0) FROM users").fetchone()
        held=c.execute("SELECT COALESCE(SUM(s.amount),0) FROM stakes s JOIN prediction p ON p.id=s.pid WHERE p.status IN ('open','closed')").fetchone()[0]
        total=c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger").fetchone()[0]
        day=datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        attendance=c.execute("SELECT COUNT(*) FROM attendance WHERE day=?",(day,)).fetchone()[0]
        active=c.execute("SELECT COUNT(*) FROM prediction WHERE status IN ('open','closed')").fetchone()[0]
        finished=c.execute("SELECT COUNT(*) FROM prediction WHERE status IN ('settled','cancelled')").fetchone()[0]
        mismatch=c.execute("SELECT COUNT(*) FROM users u WHERE u.balance != COALESCE((SELECT SUM(delta) FROM ledger l WHERE l.uid=u.uid),0)").fetchone()[0]
        integrity=c.execute("PRAGMA quick_check").fetchone()[0]=="ok"
    backups=list((path.parent/"backups").glob("*.sqlite3"))
    age=int(time.time()-max(p.stat().st_mtime for p in backups)) if backups else None
    return dict(checked_at=int(time.time()),users=users,balance=balance,held=held,circulation=balance+held,ledger_total=total,attendance_today=attendance,active_predictions=active,finished_predictions=finished,ledger_consistent=not mismatch and total==balance,db_integrity=integrity,backup_age_seconds=age)

if __name__=="__main__":
    print(json.dumps(snapshot(os.environ.get("LOG_POWER_DB","/home/ubuntu/abyss-log-power/data/points.sqlite3"))))
