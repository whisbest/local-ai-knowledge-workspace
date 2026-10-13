import signal
from .config import Config
from .db import Store
from .worker import Worker


def main():
    store=Store(Config.from_env());worker=Worker(store)
    from .backups import recover_restore
    recover_restore(store)
    signal.signal(signal.SIGTERM,lambda *_:worker.stop_event.set())
    signal.signal(signal.SIGINT,lambda *_:worker.stop_event.set())
    try:worker.run()
    finally:store.engine.dispose()


if __name__=='__main__':main()
