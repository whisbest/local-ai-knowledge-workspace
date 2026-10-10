from __future__ import annotations
import threading
from .core import Job, AppError
from .models_service import execute_pull
from .documents import execute_process


class Worker:
    """One execution thread for both local inference and background model work."""
    def __init__(self,store):
        self.store=store;self.stop_event=threading.Event();self.thread=threading.Thread(target=self.loop,daemon=True)
    def start(self):self.thread.start()
    def questions_at_boundary(self):
        id=self.store.claim('generation')
        if id:self.execute(id)
    def stop(self):
        self.stop_event.set()
        self.store.recover()
        self.thread.join(timeout=3)
    def loop(self):
        while not self.stop_event.is_set():
            if self.store.locked:self.stop_event.wait(.2);continue
            id=self.store.claim()
            if not id:self.stop_event.wait(.2);continue
            self.execute(id)
    def execute(self,id):
        with self.store.gate:self.store.inflight_workers+=1
        try:
            with self.store.session() as s:kind=s.get(Job,id).kind
            if kind=='pull':execute_pull(self.store,id)
            elif kind=='process':
                execute_process(self.store,id)
                with self.store.session() as s:current=s.get(Job,id);in_progress=current and current.state=='running'
                if in_progress:
                    from .indexes import execute_index
                    execute_index(self.store,id)
            elif kind=='generation':
                from .chat import execute_generation
                execute_generation(self.store,id)
            else:raise AppError('JOB_KIND','不支持的任务类型。')
        except Exception as e:
            with self.store.session() as s:
                j=s.get(Job,id)
                if j and j.state=='running':j.state='failed';j.error=e.message if isinstance(e,AppError) else '任务执行失败，请重试。';j.stage='failed'
                if j and j.kind=='process':
                    from .core import Record
                    d=s.get(Record,j.target)
                    if d and d.state!='deleted' and j.state=='failed':d.state='failed'
        finally:
            with self.store.gate:self.store.inflight_workers-=1
