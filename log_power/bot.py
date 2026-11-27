"""Standalone Discord gateway service. Run python -m log_power.bot."""
import os, time, uuid, asyncio, logging, sqlite3
from pathlib import Path
import discord
from discord.ext import commands, tasks
from .interaction_bridge import start_bridge
from .store import Store, CHANNEL_ID

logging.basicConfig(level=logging.INFO)
path=Path(os.environ.get("LOG_POWER_DB","log_power_data/points.sqlite3"))
path.parent.mkdir(parents=True,exist_ok=True)
db=Store(path)
intents=discord.Intents.none()
intents.guilds=True
intents.voice_states=True
intents.members=True
bot=commands.Bot(command_prefix=commands.when_mentioned,intents=intents,help_command=None)
lock=asyncio.Lock()
channel=None
last={}
session=uuid.uuid4().hex

def admin(i):
    return isinstance(i.user,discord.Member) and i.permissions.administrator

async def guard(i, privileged=False):
    if i.channel_id!=CHANNEL_ID or not channel or i.guild_id!=channel.guild.id:
        await i.response.send_message("전용 채널에서만 이용 가능합니다.",ephemeral=True)
        return False
    if privileged and not admin(i):
        await i.response.send_message("관리자 전용입니다.",ephemeral=True)
        return False
    return True

async def reply(i,text):
    if i.response.is_done():
        await i.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    else:
        await i.response.send_message(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

def get_setting(key):
    with db.tx() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return r[0] if r else None

def set_setting(key,value):
    with db.tx() as c:
        c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))

async def fetch_message(mid):
    if not mid:
        return None
    try:
        return await channel.fetch_message(int(mid))
    except discord.NotFound:
        return None

def panel_embed():
    p=db.active()
    return discord.Embed(title="🪵 ABYSS · 통나무 파워",description="음성 활동 30분마다 **500 TP**\n일일 출석 **1,000 TP**\n\n승부 예측: "+(p["question"] if p else "진행 중인 예측 없음")+"\n서버 내부 전용 포인트 · 환전 불가",color=0x36b879)

async def panel():
    m=await fetch_message(get_setting("panel"))
    if m is None:
        m=await channel.send(embed=panel_embed(),view=Panel(),allowed_mentions=discord.AllowedMentions.none())
        set_setting("panel",m.id)
    else:
        await m.edit(embed=panel_embed(),view=Panel())
    if not m.pinned:
        await m.pin(reason="통나무 파워 고정 패널")

def prediction_embed(p):
    a,b=db.totals(p["id"])
    total=a+b
    e=discord.Embed(title=p["question"],color=0x5795ed,description={"open":"참여 가능","closed":"참여 마감","settled":"정산 완료","cancelled":"전액 환불"}[p["status"]])
    for label,n in [(p["a"],a),(p["b"],b)]:
        odds=f"{total/n:.2f}배" if n and a and b else "미확정"
        rank="정배" if n>total-n else "역배" if n<total-n else "동배"
        e.add_field(name=label,value=f"{n:,} TP · {odds} · {rank}")
    if p["status"]=="settled":
        e.add_field(name="결과",value=p["a"] if p["winner"]==0 else p["b"])
    if p["deadline"] and p["status"]=="open":
        e.add_field(name="마감",value=f"<t:{p['deadline']}:R>",inline=False)
    return e

def prediction(pid):
    with db.tx() as c:
        r=c.execute("SELECT * FROM prediction WHERE id=?",(pid,)).fetchone()
        return dict(r) if r else None

async def render(pid):
    p=prediction(pid)
    if not p:
        return
    m=await fetch_message(p["message"])
    view=BetView(pid) if p["status"]=="open" else None
    if m is None and p["status"] in ("open","closed"):
        m=await channel.send(embed=prediction_embed(p),view=view,allowed_mentions=discord.AllowedMentions.none())
        with db.tx() as c:
            c.execute("UPDATE prediction SET message=? WHERE id=?",(str(m.id),pid))
    elif m:
        await m.edit(embed=prediction_embed(p),view=view)

class BetModal(discord.ui.Modal,title="통나무 파워 참여"):
    amount=discord.ui.TextInput(label="참여량 (최소 100 TP)",max_length=10)
    def __init__(self,pid,side):
        super().__init__(custom_id=f"lp:modal:bet:{uuid.uuid4().hex}",timeout=300)
        self.pid,self.side=pid,side
    async def on_submit(self,i):
        if not await guard(i): return
        await i.response.defer(ephemeral=True)
        try:
            async with lock:
                db.bet(self.pid,i.user.id,self.side,int(self.amount.value),f"bet:{i.id}",int(time.time()))
                await render(self.pid)
            await reply(i,"참여 완료")
        except ValueError as e: await reply(i,str(e))

class BetView(discord.ui.View):
    def __init__(self,pid):
        super().__init__(timeout=None)
        p=prediction(pid)
        for side,label in [(0,p["a"][:70]+" 참여"),(1,p["b"][:70]+" 참여")]:
            b=discord.ui.Button(label=label,custom_id=f"lp:bet:{pid}:{side}",style=discord.ButtonStyle.primary if side==0 else discord.ButtonStyle.danger)
            async def callback(i,s=side):
                if await guard(i): await i.response.send_modal(BetModal(pid,s))
            b.callback=callback
            self.add_item(b)

class CreateModal(discord.ui.Modal,title="승부 예측 시작"):
    def __init__(self):
        super().__init__(custom_id=f"lp:modal:create:{uuid.uuid4().hex}",timeout=300)
    question=discord.ui.TextInput(label="예측 주제",max_length=200)
    a=discord.ui.TextInput(label="A 선택지",max_length=80)
    b=discord.ui.TextInput(label="B 선택지",max_length=80)
    minutes=discord.ui.TextInput(label="참여 시간(분), 0은 수동 마감",default="5",max_length=4)
    async def on_submit(self,i):
        if not await guard(i,True): return
        await i.response.defer(ephemeral=True)
        try:
            n=int(self.minutes.value)
            if not 0<=n<=1440: raise ValueError("시간 범위 오류")
            async with lock:
                pid=db.create(str(self.question.value),str(self.a.value),str(self.b.value),int(time.time())+60*n if n else None)
                await render(pid)
                await panel()
            await reply(i,"예측 생성 완료")
        except ValueError as e: await reply(i,str(e))

class GrantModal(discord.ui.Modal,title="통나무 지급 / 회수"):
    def __init__(self):
        super().__init__(custom_id=f"lp:modal:grant:{uuid.uuid4().hex}",timeout=300)
    targets=discord.ui.TextInput(label="사용자 ID / all / voice / role:ID",max_length=1000)
    amount=discord.ui.TextInput(label="1인당 지급량 (회수는 음수)",max_length=10)
    reason=discord.ui.TextInput(label="사유",max_length=150)
    async def on_submit(self,i):
        if not await guard(i,True): return
        await i.response.defer(ephemeral=True)
        raw=self.targets.value.strip()
        try:
            if raw=="all": members=[m async for m in i.guild.fetch_members(limit=None)]
            elif raw=="voice": members=[m for c in i.guild.voice_channels for m in c.members]
            elif raw.startswith("role:"):
                role=i.guild.get_role(int(raw[5:]))
                if role is None: raise ValueError("역할 없음")
                members=[m async for m in i.guild.fetch_members(limit=None) if role in m.roles]
            else: members=[await i.guild.fetch_member(int(x.strip())) for x in raw.split(",")]
            ids=sorted({m.id for m in members if not m.bot})
            n=int(self.amount.value)
            if not ids or not 0<abs(n)<=100000000: raise ValueError("대상 또는 금액 오류")
        except (ValueError,discord.HTTPException) as e:
            await reply(i,str(e)); return
        await i.followup.send(f"{len(ids)}명 × {n:,} TP = {len(ids)*n:,} TP\n사유: {self.reason.value}",view=GrantConfirm(i.user.id,ids,n,self.reason.value,str(i.id)),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

class GrantConfirm(discord.ui.View):
    def __init__(self,actor,ids,n,reason,key):
        super().__init__(timeout=60)
        self.actor,self.ids,self.n,self.reason,self.key=actor,ids,n,reason,key
        self.confirm.custom_id=f"lp:confirm:grant:{uuid.uuid4().hex}"
    @discord.ui.button(label="지급/회수 확정",style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        if not await guard(i,True): return
        if i.user.id!=self.actor: await reply(i,"요청자만 확정 가능합니다."); return
        try:
            count=db.grant(self.ids,self.n,"grant:"+self.key,self.reason,self.actor)
            await i.response.edit_message(content=f"{count}명 처리 완료",view=None)
        except ValueError as e: await reply(i,str(e))

class ResultConfirm(discord.ui.View):
    def __init__(self,pid,winner,actor):
        super().__init__(timeout=60)
        self.pid,self.winner,self.actor=pid,winner,actor
        self.confirm.custom_id=f"lp:confirm:result:{uuid.uuid4().hex}"
    @discord.ui.button(label="최종 확정",style=discord.ButtonStyle.danger)
    async def confirm(self,i,b):
        if not await guard(i,True): return
        if i.user.id!=self.actor: await reply(i,"요청자만 확정 가능합니다."); return
        await i.response.defer(ephemeral=True)
        try:
            async with lock:
                refund=db.settle(self.pid,self.winner,int(time.time()),i.user.id)
                await render(self.pid)
                await panel()
            await reply(i,"환불 완료" if refund else "정산 완료")
        except ValueError as e: await reply(i,str(e))

class Manage(discord.ui.View):
    def __init__(self,pid):
        super().__init__(timeout=120)
        self.pid=pid
        self.close.custom_id=f"lp:manage:close:{uuid.uuid4().hex}"
        for label,side in [("A 결과 확정",0),("B 결과 확정",1),("취소 및 환불",None)]:
            b=discord.ui.Button(label=label,style=discord.ButtonStyle.danger,custom_id=f"lp:manage:result:{uuid.uuid4().hex}")
            async def cb(i,s=side):
                if await guard(i,True):
                    await i.response.send_message("정산 또는 환불을 확정하시겠습니까?",view=ResultConfirm(pid,s,i.user.id),ephemeral=True)
            b.callback=cb
            self.add_item(b)
    @discord.ui.button(label="예측 마감")
    async def close(self,i,b):
        if not await guard(i,True): return
        await i.response.defer(ephemeral=True)
        async with lock:
            db.close(self.pid)
            await render(self.pid)
        await reply(i,"마감 완료")

class UserLookup(discord.ui.Modal,title="사용자 통나무 조회"):
    def __init__(self):
        super().__init__(custom_id=f"lp:modal:lookup:{uuid.uuid4().hex}",timeout=300)
    uid=discord.ui.TextInput(label="사용자 ID",max_length=20)
    async def on_submit(self,i):
        if not await guard(i,True): return
        uid=self.uid.value.strip()
        if not uid.isdigit(): await reply(i,"사용자 ID를 확인하세요."); return
        balance,seconds=db.balance(uid)
        with db.tx() as c:
            rows=list(c.execute("SELECT delta,reason,created FROM ledger WHERE uid=? AND delta!=0 ORDER BY rowid DESC LIMIT 10",(uid,)))
        await reply(i,f"<@{uid}> · {balance:,} TP · 음성 {seconds//60}분\n"+"\n".join(f"{r[2]} · {r[0]:+,} TP · {r[1][:70]}" for r in rows))

class Panel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="출석 체크",custom_id="lp:attendance")
    async def attendance(self,i,b):
        if await guard(i): await reply(i,"+1,000 TP 지급" if db.attend(i.user.id) else "오늘 이미 출석했습니다.")
    @discord.ui.button(label="내 통나무",custom_id="lp:balance")
    async def balance(self,i,b):
        if await guard(i):
            n,s=db.balance(i.user.id)
            await reply(i,f"보유 {n:,} TP · 누적 음성 {s//60:,}분 · 다음 지급까지 {(1800-s%1800+59)//60}분")
    @discord.ui.button(label="전체 랭킹",custom_id="lp:rank")
    async def rank(self,i,b):
        if await guard(i): await reply(i,"\n".join(f"{k}. <@{u}> · {n:,} TP" for k,(u,n) in enumerate(db.ranking(),1)) or "기록 없음")
    @discord.ui.button(label="예측 기록",custom_id="lp:history")
    async def history(self,i,b):
        if not await guard(i): return
        with db.tx() as c:
            rows=list(c.execute("SELECT question,status,winner,a,b FROM prediction WHERE status IN ('settled','cancelled') ORDER BY id DESC LIMIT 8"))
        await reply(i,"\n".join(f"{r[0][:100]} · "+("환불" if r[1]=="cancelled" else r[3+r[2]]) for r in rows) or "기록 없음")
    @discord.ui.button(label="예측 시작 / 관리",style=discord.ButtonStyle.success,custom_id="lp:manage")
    async def manage(self,i,b):
        if not await guard(i,True): return
        p=db.active()
        if p: await i.response.send_message(p["question"],view=Manage(p["id"]),ephemeral=True)
        else: await i.response.send_modal(CreateModal())
    @discord.ui.button(label="통나무 지급 / 회수",custom_id="lp:grant")
    async def grant(self,i,b):
        if await guard(i,True): await i.response.send_modal(GrantModal())
    @discord.ui.button(label="사용자 조회",custom_id="lp:lookup")
    async def lookup(self,i,b):
        if await guard(i,True): await i.response.send_modal(UserLookup())
    @discord.ui.button(label="통나무 관리",custom_id="lp:stats")
    async def stats(self,i,b):
        if not await guard(i,True): return
        with db.tx() as c:
            n,total=c.execute("SELECT COUNT(*),COALESCE(SUM(balance),0) FROM users").fetchone()
            held=c.execute("SELECT COALESCE(SUM(s.amount),0) FROM stakes s JOIN prediction p ON p.id=s.pid WHERE p.status IN ('open','closed')").fetchone()[0]
            rows=list(c.execute("SELECT uid,delta,reason FROM ledger WHERE delta!=0 ORDER BY rowid DESC LIMIT 5"))
        await reply(i,f"등록 {n}명 · 잔액 {total:,} TP · 예측 보관 {held:,} TP\n"+"\n".join(f"<@{r[0]}> {r[1]:+,} TP · {r[2][:70]}" for r in rows))

def eligible():
    if not channel: return set()
    return {m.id for c in channel.guild.voice_channels if c.id!=channel.guild.afk_channel_id and len([m for m in c.members if not m.bot])>=2 for m in c.members if not m.bot}

def tick_voice():
    now=time.monotonic()
    current=eligible()
    for uid,old in list(last.items()):
        elapsed=min(35,int(now-old))
        if elapsed>0:
            db.voice(uid,elapsed,f"voice:{session}:{uid}:{old}")
    last.clear()
    last.update({uid:now for uid in current})

@bot.event
async def on_voice_state_update(member,before,after):
    if channel and member.guild.id==channel.guild.id:
        tick_voice()

@bot.event
async def on_disconnect():
    last.clear()

@tasks.loop(seconds=15)
async def maintenance():
    try:
        await maintenance_once()
    except Exception:
        logging.exception("maintenance retry on next tick")

async def maintenance_once():
    if not bot.is_ready() or not channel: return
    tick_voice()
    async with lock:
        p=db.active()
        now=int(time.time())
        if p and p["status"]=="open" and p["deadline"] and now>=p["deadline"]:
            db.close(p["id"])
            await render(p["id"])
        with db.tx() as c:
            rows=list(c.execute("SELECT id,message FROM prediction WHERE delete_at<=? AND message IS NOT NULL",(now,)))
        for pid,mid in rows:
            m=await fetch_message(mid)
            if m: await m.delete()
            with db.tx() as c:
                c.execute("UPDATE prediction SET message=NULL WHERE id=?",(pid,))
        if await fetch_message(get_setting("panel")) is None: await panel()

@maintenance.error
async def maintenance_error(e):
    logging.exception("maintenance failed",exc_info=e)

@bot.event
async def on_ready():
    global channel
    channel=await bot.fetch_channel(CHANNEL_ID)
    if not isinstance(channel,discord.TextChannel): raise RuntimeError("전용 텍스트 채널 필요")
    if not getattr(bot,"lp_registered",False):
        bot.add_view(Panel())
        bot.lp_registered=True
        p=db.active()
        if p and p["status"]=="open" and p["message"]: bot.add_view(BetView(p["id"]),message_id=int(p["message"]))
    async with lock:
        await panel()
        p=db.active()
        if p: await render(p["id"])
    last.clear()
    tick_voice()
    if not maintenance.is_running(): maintenance.start()
    if not getattr(bot,"lp_bridge",None):
        bot.lp_bridge=await start_bridge(bot)

if __name__=="__main__":
    bot.run(os.environ["DISCORD_BOT_TOKEN"])
