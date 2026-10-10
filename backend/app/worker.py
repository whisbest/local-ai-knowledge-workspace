from __future__ import annotations
import threading
from .core import Job, AppError
from .models_service import execute_pull


class Worker:
    """One execution thread for both local inference and background model work."""
    def __init__(self,store):
        self.store=store;self.stop_event=threading.Event();self.thread=threading.Thread(target=self.loop,daemon=True)
    def start(self):self.thread.start()
    def stop(self):self.stop_event.set();self.thread.join(timeout=2)
    def loop(self):
        while not self.stop_event.is_set():
            if self.store.locked:self.stop_event.wait(.2);continue
            id=self.store.claim('pull')
            if not id:self.stop_event.wait(.2);continue
            self.execute(id)
    def execute(self,id):
        try:execute_pull(self.store,id)
        except Exception as e:
            with self.store.session() as s:
                j=s.get(Job,id)
                if j and j.state=='running':j.state='failed';j.error=e.message if isinstance(e,AppError) else '任务执行失败，请重试。';j.stage='failed'
