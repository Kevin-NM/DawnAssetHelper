import os
import subprocess
import threading
import time
from typing import Tuple, List

class ProcessRunner:
    def __init__(self, args: List[str], cwd: str | None = None):
        self.args = args
        self.cwd = cwd
        self.process = None
        self.stdout_lines = []
        self.stderr_lines = []
        self.is_cancelled = False
        self._lock = threading.Lock()

    def run(self, timeout: int = None, stdout_callback=None, stderr_callback=None) -> Tuple[int, str, str]:
        try:
            startupinfo = None
            creationflags = 0
            if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            self.process = subprocess.Popen(
                self.args,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=creationflags
            )
        except Exception as e:
            return -1, "", str(e)

        def read_stream(stream, lines_list, callback):
            for line in iter(stream.readline, ''):
                line_stripped = line.rstrip()
                with self._lock:
                    lines_list.append(line_stripped)
                if callback:
                    callback(line_stripped)
            stream.close()

        stdout_thread = threading.Thread(target=read_stream, args=(self.process.stdout, self.stdout_lines, stdout_callback))
        stderr_thread = threading.Thread(target=read_stream, args=(self.process.stderr, self.stderr_lines, stderr_callback))

        stdout_thread.start()
        stderr_thread.start()

        start_time = time.time()
        timed_out = False
        while self.process.poll() is None:
            if self.is_cancelled:
                self._terminate_process()
                break
            if timeout and (time.time() - start_time) > timeout:
                timed_out = True
                self._terminate_process()
                break
            time.sleep(0.1)

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        if self.is_cancelled:
            exit_code = -1
        elif timed_out:
            exit_code = -2
        else:
            exit_code = self.process.returncode if self.process else -1
        
        with self._lock:
            stdout_str = "\n".join(self.stdout_lines)
            stderr_str = "\n".join(self.stderr_lines)

        return exit_code, stdout_str, stderr_str

    def _terminate_process(self):
        if not self.process or self.process.poll() is not None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass

    def cancel(self):
        self.is_cancelled = True
        self._terminate_process()
