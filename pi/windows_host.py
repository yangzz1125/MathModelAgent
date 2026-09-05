"""Windows process-tree and crash-safe Host-state boundary for contract-v3."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _kernel32() -> ctypes.WinDLL:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    kernel.SetFilePointerEx.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.SetEndOfFile.argtypes = (wintypes.HANDLE,)
    kernel.SetEndOfFile.restype = wintypes.BOOL
    kernel.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    return kernel


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _checksum(generation: int, state: dict[str, Any]) -> str:
    payload = json.dumps(
        {"generation": generation, "state": state},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _envelope(generation: int, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generation": generation,
        "state": state,
        "sha256": _checksum(generation, state),
    }


def _valid_envelope(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        generation = value["generation"]
        state = value["state"]
        if (
            value.get("schema_version") == 1
            and isinstance(generation, int)
            and isinstance(state, dict)
            and isinstance(state.get("project"), dict)
            and isinstance(state.get("ledger"), dict)
            and value.get("sha256") == _checksum(generation, state)
        ):
            return value
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        pass
    return None


class WindowsHostBoundary:
    """Lock Host mirrors, journal them, and own the Pi process tree."""

    def __init__(self, task_id: str, workspace: Path) -> None:
        if os.name != "nt":
            raise RuntimeError(
                "contract-v3 requires Windows Host locking or an external sandbox"
            )
        self.task_id = task_id
        self.workspace = workspace
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        self.journal_paths = (
            base / "MathModelAgentPi" / "state" / f"{task_id}.a.json",
            base / "MathModelAgentPi" / "state" / f"{task_id}.b.json",
        )
        self.handles: dict[Path, int] = {}
        self.job_handle: int | None = None
        self.job_assigned = False
        self.generation = 0
        self.state: dict[str, Any] = {}
        self.kernel = _kernel32()

    @classmethod
    def recover(cls, task_id: str, workspace: Path) -> None:
        if os.name != "nt":
            return
        boundary = cls(task_id, workspace)
        candidates = [
            value
            for value in (_valid_envelope(path) for path in boundary.journal_paths)
            if value is not None
        ]
        if not candidates:
            return
        latest = max(candidates, key=lambda value: value["generation"])
        try:
            project = json.loads((workspace / "project.json").read_text(encoding="utf-8"))
            ledger = json.loads((workspace / "planning" / "ledger.json").read_text(encoding="utf-8"))
            mirror_state = {"project": project, "ledger": ledger}
            if mirror_state == latest["state"]:
                return
            if not all(
                any(mirror_state[field] == candidate["state"][field] for candidate in candidates)
                for field in ("project", "ledger")
            ):
                return
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        state = latest["state"]
        _atomic_json(workspace / "project.json", state["project"])
        _atomic_json(workspace / "planning" / "ledger.json", state["ledger"])

    def acquire(self) -> None:
        project_path = self.workspace / "project.json"
        ledger_path = self.workspace / "planning" / "ledger.json"
        if not project_path.is_file() or not ledger_path.is_file():
            raise RuntimeError("contract-v3 requires project.json and planning/ledger.json")
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.recover(self.task_id, self.workspace)
            project = json.loads(project_path.read_text(encoding="utf-8"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if (project.get("workflow") or {}).get("contract_version") not in {3, 4}:
            raise RuntimeError("WindowsHostBoundary requires contract-v3 or v4")
        candidates = [
            value
            for value in (_valid_envelope(path) for path in self.journal_paths)
            if value is not None
        ]
        if candidates:
            latest = max(candidates, key=lambda value: value["generation"])
            self.generation = latest["generation"]
            mirror_state = {"project": project, "ledger": ledger}
            known_mirror = all(
                any(mirror_state[field] == candidate["state"][field] for candidate in candidates)
                for field in ("project", "ledger")
            )
            if mirror_state == latest["state"]:
                self.state = copy.deepcopy(mirror_state)
            elif known_mirror:
                self.state = copy.deepcopy(latest["state"])
                _atomic_json(project_path, self.state["project"])
                _atomic_json(ledger_path, self.state["ledger"])
            else:
                self.generation += 1
                self.state = {
                    "project": copy.deepcopy(project),
                    "ledger": copy.deepcopy(ledger),
                }
                _atomic_json(
                    self.journal_paths[self.generation % 2],
                    _envelope(self.generation, self.state),
                )
        else:
            self.state = {"project": copy.deepcopy(project), "ledger": copy.deepcopy(ledger)}
            _atomic_json(self.journal_paths[0], _envelope(0, self.state))
            _atomic_json(self.journal_paths[1], _envelope(-1, self.state))
        try:
            for path in (project_path, ledger_path, *self.journal_paths):
                self.handles[path] = self._open_locked(path)
        except Exception:
            self.release()
            raise

    def _open_locked(self, path: Path) -> int:
        handle = self.kernel.CreateFileW(
            str(path),
            0x80000000 | 0x40000000,
            0x00000001,
            None,
            3,
            0x00000080,
            None,
        )
        value = int(handle) if handle else 0
        if not value or value == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), f"cannot lock Host state: {path}")
        return value

    def _write_handle(self, path: Path, payload: bytes) -> None:
        handle = self.handles[path]
        if not self.kernel.SetFilePointerEx(handle, 0, None, 0):
            raise OSError(ctypes.get_last_error(), f"cannot seek Host state: {path}")
        written = wintypes.DWORD(0)
        buffer = ctypes.create_string_buffer(payload)
        if not self.kernel.WriteFile(
            handle, buffer, len(payload), ctypes.byref(written), None
        ) or written.value != len(payload):
            raise OSError(ctypes.get_last_error(), f"cannot write Host state: {path}")
        if not self.kernel.SetEndOfFile(handle):
            raise OSError(ctypes.get_last_error(), f"cannot truncate Host state: {path}")
        if not self.kernel.FlushFileBuffers(handle):
            raise OSError(ctypes.get_last_error(), f"cannot flush Host state: {path}")

    def _save(self, field: str, value: dict[str, Any]) -> None:
        self.state[field] = copy.deepcopy(value)
        self.generation += 1
        journal_path = self.journal_paths[self.generation % 2]
        self._write_handle(journal_path, _json_bytes(_envelope(self.generation, self.state)))
        mirror = self.workspace / (
            "project.json" if field == "project" else "planning/ledger.json"
        )
        self._write_handle(mirror, _json_bytes(self.state[field]))

    def save_project(self, value: dict[str, Any]) -> None:
        self._save("project", value)

    def save_ledger(self, value: dict[str, Any]) -> None:
        self._save("ledger", value)

    def assign_and_resume(self, pid: int) -> None:
        job = self.kernel.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "cannot create Pi Job Object")
        self.job_handle = int(job)
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(
            self.job_handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise OSError(ctypes.get_last_error(), "cannot configure Pi Job Object")
        process = self.kernel.OpenProcess(
            PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION,
            False,
            pid,
        )
        if not process:
            raise OSError(ctypes.get_last_error(), "cannot open suspended Pi process")
        try:
            if not self.kernel.AssignProcessToJobObject(self.job_handle, process):
                raise OSError(ctypes.get_last_error(), "cannot assign Pi Job Object")
            self.job_assigned = True
        finally:
            if not self.kernel.CloseHandle(process):
                raise OSError(ctypes.get_last_error(), "cannot close Pi process handle")
        snapshot = self.kernel.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if int(snapshot) == INVALID_HANDLE_VALUE:
            raise OSError(ctypes.get_last_error(), "cannot enumerate suspended Pi thread")
        resumed = False
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            has_entry = bool(self.kernel.Thread32First(snapshot, ctypes.byref(entry)))
            while has_entry:
                if entry.th32OwnerProcessID == pid:
                    thread = self.kernel.OpenThread(
                        THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
                    )
                    if thread:
                        try:
                            result = self.kernel.ResumeThread(thread)
                            if result == 0xFFFFFFFF:
                                raise OSError(
                                    ctypes.get_last_error(), "cannot resume Pi thread"
                                )
                            resumed = True
                        finally:
                            if not self.kernel.CloseHandle(thread):
                                raise OSError(
                                    ctypes.get_last_error(), "cannot close Pi thread handle"
                                )
                has_entry = bool(self.kernel.Thread32Next(snapshot, ctypes.byref(entry)))
        finally:
            if not self.kernel.CloseHandle(snapshot):
                raise OSError(ctypes.get_last_error(), "cannot close thread snapshot")
        if not resumed:
            raise RuntimeError("suspended Pi process has no resumable thread")

    def terminate_job(self) -> None:
        if self.job_handle is None:
            return
        if not self.kernel.TerminateJobObject(self.job_handle, 1):
            raise OSError(ctypes.get_last_error(), "cannot terminate Pi Job Object")
        if not self.kernel.CloseHandle(self.job_handle):
            raise OSError(ctypes.get_last_error(), "cannot close Pi Job Object")
        self.job_handle = None
        self.job_assigned = False

    def release(self) -> None:
        if self.job_handle is not None:
            raise RuntimeError("cannot release Host state while Pi Job Object is active")
        errors = []
        for path, handle in list(self.handles.items()):
            if not self.kernel.CloseHandle(handle):
                errors.append(f"cannot close Host-state handle: {path}")
            else:
                self.handles.pop(path, None)
        if errors:
            raise OSError("; ".join(errors))
