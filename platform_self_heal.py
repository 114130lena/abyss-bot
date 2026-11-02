"""Worker watchdog shared by all notification platforms.

Only recovery action: restart the bot *process* via systemd on a genuinely
dead or long-stalled worker. Never resend alerts or alter dedupe files.
"""
import logging
import os
import threading
import time

class Supervisor:
    def __init__(self, stall_seconds=480, interval_seconds=30):
        self.stall_seconds = stall_seconds
        self.interval_seconds = interval_seconds
        self._lock = threading.RLock()
        self._states = {}
        self._watchdog = None

    def register(self, name):
        with self._lock:
            self._states[name] = {"start":time.monotonic(),"busy":False,"last":time.monotonic()}

    def begin(self, name):
        with self._lock:
            self._states[name] = {"start":time.monotonic(),"busy":True,"last":time.monotonic()}

    def finish(self, name):
        with self._lock:
            state=self._states.get(name)
            if state:
                state["busy"]=False
                state["last"]=time.monotonic()

    def problems(self, live_names=None, now=None):
        if now is None: now=time.monotonic()
        if live_names is None:
            live_names={t.name for t in threading.enumerate() if t.is_alive()}
        out=[]
        with self._lock:
            for name,state in self._states.items():
                if name not in live_names:
                    out.append((name,"dead"))
                elif state["busy"] and now-state["start"]>self.stall_seconds:
                    out.append((name,"stalled"))
                elif now-state["last"]>self.stall_seconds:
                    out.append((name,"no heartbeat"))
        return out

    def start_watchdog(self):
        if self._watchdog and self._watchdog.is_alive(): return
        self._watchdog=threading.Thread(target=self._loop,name="notification-watchdog",daemon=True)
        self._watchdog.start()

    def _loop(self):
        # Two consecutive unhealthy readings avoid recovery on transient
        # thread startup / scheduling issues.
        previous=set()
        while True:
            time.sleep(self.interval_seconds)
            current=set(self.problems())
            if current:
                logging.error("PLATFORM_SELF_HEAL worker_unhealthy=%s",sorted(current))
            repeated=current & previous
            if repeated:
                logging.critical("PLATFORM_SELF_HEAL restart_required=%s",sorted(repeated))
                # systemd configured with Restart=on-failure. Exit nonzero
                # rather than creating duplicate polling threads.
                os._exit(74)
            previous=current
