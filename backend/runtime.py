"""Keep LangChain and model initialization off the authentication/startup path."""
from functools import lru_cache
import threading

_lock = threading.Lock()


@lru_cache(maxsize=1)
def _create_engine():
    from agent.react_agent import ReactAgent
    return ReactAgent()


def create_engine():
    with _lock:
        return _create_engine()
