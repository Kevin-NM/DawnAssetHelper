"""Lightweight stubs for PySide6 classes used by orchestrator/logger.
Allows running the web server without installing PySide6."""
import threading


class _SignalInstance:
    """Per-instance signal that stores connected slots."""
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        if slot not in self._slots:
            self._slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self._slots.clear()
        elif slot in self._slots:
            self._slots.remove(slot)

    def emit(self, *args):
        for slot in self._slots[:]:
            try:
                slot(*args)
            except Exception:
                pass


class _SignalDescriptor:
    """Descriptor that creates a _SignalInstance per owner object, mimicking PySide6 Signal."""
    def __init__(self, *types):
        self._types = types
        self._instances = {}

    def __set_name__(self, owner, name):
        self._name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        obj_id = id(obj)
        if obj_id not in self._instances:
            self._instances[obj_id] = _SignalInstance()
        return self._instances[obj_id]


def Signal(*types):
    return _SignalDescriptor(*types)


class QObject:
    pass


class QThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super().__init__(daemon=True)
        self._is_running = False

    def start(self):
        self._is_running = True
        super().start()

    def isRunning(self):
        return self._is_running and self.is_alive()

    def run(self):
        pass

    def quit(self):
        self._is_running = False
