"""Independent SQLite ledger for non-redeemable community points."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

CHANNEL_ID = 1575809466576932904
KST = timezone(timedelta(hours=9))

class Store:
    def __init__(self, path):
        self.path = path
        with self.tx() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0), seconds INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, uid TEXT NOT NULL, delta INTEGER NOT NULL, reason TEXT NOT NULL, actor TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS attendance(uid TEXT, day TEXT, PRIMARY KEY(uid,day));
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS prediction(id INTEGER PRIMARY KEY, question TEXT, a TEXT, b TEXT, status TEXT, deadline INTEGER, winner INTEGER, message TEXT, delete_at INTEGER);
            CREATE TABLE IF NOT EXISTS stakes(pid INTEGER,uid TEXT,side INTEGER CHECK(side IN (0,1)),amount INTEGER CHECK(amount>0),PRIMARY KEY(pid,uid));
            """)
    @contextmanager
    def tx(self):
        c=sqlite3.connect(self.path, timeout=15)
        c.row_factory=sqlite3.Row
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
    def change(self,c,uid,delta,key,reason,actor=None):
        uid=str(uid)
        if c.execute("SELECT 1 FROM ledger WHERE id=?",(key,)).fetchone():
            return False
        c.execute("INSERT OR IGNORE INTO users(uid) VALUES(?)",(uid,))
        if c.execute("UPDATE users SET balance=balance+? WHERE uid=? AND balance+?>=0",(delta,uid,delta)).rowcount!=1:
            raise ValueError("잔액이 부족합니다.")
        c.execute("INSERT INTO ledger(id,uid,delta,reason,actor) VALUES(?,?,?,?,?)",(key,uid,delta,reason,actor))
        return True
    def attend(self,uid):
        day=datetime.now(KST).date().isoformat()
        with self.tx() as c:
            if not c.execute("INSERT OR IGNORE INTO attendance VALUES(?,?)",(str(uid),day)).rowcount:
                return False
            return self.change(c,uid,1000,f"attendance:{uid}:{day}","출석")
    def voice(self,uid,seconds,key):
        with self.tx() as c:
            uid=str(uid)
            if c.execute("SELECT 1 FROM ledger WHERE id=?",(key,)).fetchone():
                return
            c.execute("INSERT OR IGNORE INTO users(uid) VALUES(?)",(uid,))
            old=c.execute("SELECT seconds FROM users WHERE uid=?",(uid,)).fetchone()[0]
            new=old+max(0,int(seconds))
            c.execute("UPDATE users SET seconds=? WHERE uid=?",(new,uid))
            self.change(c,uid,(new//1800-old//1800)*500,key,"음성 활동")
    def grant(self,uids,amount,key,reason,actor):
        if not isinstance(amount,int) or not 0<abs(amount)<=100000000:
            raise ValueError("지급량 오류")
        with self.tx() as c:
            return sum(self.change(c,u,amount,f"{key}:{u}",reason,str(actor)) for u in sorted(set(map(str,uids))))
    def balance(self,uid):
        with self.tx() as c:
            r=c.execute("SELECT balance,seconds FROM users WHERE uid=?",(str(uid),)).fetchone()
            return tuple(r) if r else (0,0)
    def ranking(self):
        with self.tx() as c:
            return [tuple(r) for r in c.execute("SELECT uid,balance FROM users ORDER BY balance DESC,uid LIMIT 10")]
    def create(self,q,a,b,deadline):
        if not q.strip() or not a.strip() or not b.strip() or a.strip()==b.strip():
            raise ValueError("주제 및 선택지를 확인하세요.")
        with self.tx() as c:
            if c.execute("SELECT 1 FROM prediction WHERE status IN ('open','closed')").fetchone():
                raise ValueError("진행 중인 예측이 있습니다.")
            return c.execute("INSERT INTO prediction(question,a,b,status,deadline) VALUES(?,?,?,'open',?)",(q,a,b,deadline)).lastrowid
    def active(self):
        with self.tx() as c:
            r=c.execute("SELECT * FROM prediction WHERE status IN ('open','closed') ORDER BY id DESC LIMIT 1").fetchone()
            return dict(r) if r else None
    def totals(self,pid):
        with self.tx() as c:
            out=[0,0]
            for side,amount in c.execute("SELECT side,SUM(amount) FROM stakes WHERE pid=? GROUP BY side",(pid,)):
                out[side]=amount
            return out
    def bet(self,pid,uid,side,amount,key,now):
        if side not in (0,1) or not isinstance(amount,int) or amount<100:
            raise ValueError("최소 100 TP")
        with self.tx() as c:
            p=c.execute("SELECT * FROM prediction WHERE id=?",(pid,)).fetchone()
            if not p or p["status"]!="open" or (p["deadline"] and now>=p["deadline"]):
                raise ValueError("예측이 마감되었습니다.")
            uid=str(uid)
            r=c.execute("SELECT side FROM stakes WHERE pid=? AND uid=?",(pid,uid)).fetchone()
            if r and r[0]!=side:
                raise ValueError("선택지 변경 불가")
            if self.change(c,uid,-amount,key,"예측 참여"):
                c.execute("INSERT INTO stakes VALUES(?,?,?,?) ON CONFLICT(pid,uid) DO UPDATE SET amount=amount+excluded.amount",(pid,uid,side,amount))
    def close(self,pid):
        with self.tx() as c:
            c.execute("UPDATE prediction SET status='closed' WHERE id=? AND status='open'",(pid,))
    def settle(self,pid,winner,now,actor):
        if winner not in (None,0,1):
            raise ValueError("결과 오류")
        with self.tx() as c:
            p=c.execute("SELECT * FROM prediction WHERE id=?",(pid,)).fetchone()
            if not p or p["status"] not in ("open","closed") or (winner is not None and p["status"]!="closed"):
                raise ValueError("정산할 수 없는 상태입니다.")
            rows=list(c.execute("SELECT * FROM stakes WHERE pid=? ORDER BY uid",(pid,)))
            total=sum(r["amount"] for r in rows)
            winning=[r for r in rows if r["side"]==winner]
            denom=sum(r["amount"] for r in winning)
            refund=winner is None or not denom or denom==total
            payouts={r["uid"]:r["amount"] for r in rows} if refund else {r["uid"]:total*r["amount"]//denom for r in winning}
            if not refund:
                remaining=total-sum(payouts.values())
                order=sorted(winning,key=lambda r:(-(total*r["amount"]%denom),r["uid"]))
                for r in order[:remaining]:
                    payouts[r["uid"]]+=1
            for uid,amount in payouts.items():
                self.change(c,uid,amount,f"settle:{pid}:{uid}","예측 환불" if refund else "예측 정산",str(actor))
            c.execute("UPDATE prediction SET status=?,winner=?,delete_at=? WHERE id=?",("cancelled" if refund else "settled",winner,now+300,pid))
            return refund
