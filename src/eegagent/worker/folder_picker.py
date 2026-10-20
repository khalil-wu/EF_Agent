"""Select an existing local folder with the OS dialog in a dedicated GUI process."""
import json
import os
from pathlib import Path
import subprocess
import sys

TITLE = "选择 EFAgent 工作区文件夹"


def pick_directory() -> str | None:
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + environment.get("PYTHONPATH", "")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run([sys.executable, "-m", "eegagent.worker.folder_picker"],
                            capture_output=True, text=True, encoding="utf-8", env=environment, **options)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"文件夹选择器退出，代码 {result.returncode}")
    response = json.loads(result.stdout)
    return None if response.get("cancelled") else response["path"]


def _choose_windows_directory() -> str | None:
    """IFileOpenDialog with PICKFOLDERS uses Explorer's native folder UI."""
    import ctypes
    from ctypes import wintypes
    from uuid import UUID

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

    def method(pointer, index, *arguments):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(table[index])

    def check(status, operation):
        if status < 0:
            raise OSError(status, f"文件夹选择器 {operation} 失败 (0x{status & 0xffffffff:08X})")

    ole32, user32 = ctypes.windll.ole32, ctypes.windll.user32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    check(ole32.CoInitializeEx(None, 2), "初始化")  # COM apartment runs on this process's main thread.
    owner = user32.CreateWindowExW(8, "Static", TITLE, 0x80000000, 0, 0, 1, 1, None, None, None, None)
    dialog, item, display_name = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if not owner:
            raise ctypes.WinError()
        clsid = GUID.from_buffer_copy(UUID("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7").bytes_le)
        interface = GUID.from_buffer_copy(UUID("D57C7288-D4AD-4768-BE02-9D969532D960").bytes_le)
        check(ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(interface), ctypes.byref(dialog)), "创建窗口")
        options = wintypes.DWORD()
        check(method(dialog, 10, ctypes.POINTER(wintypes.DWORD))(dialog, ctypes.byref(options)), "读取选项")
        # PICKFOLDERS | FORCEFILESYSTEM | PATHMUSTEXIST; the invisible topmost owner keeps the dialog in front.
        check(method(dialog, 9, wintypes.DWORD)(dialog, options.value | 0x20 | 0x40 | 0x800), "设置选项")
        check(method(dialog, 17, wintypes.LPCWSTR)(dialog, TITLE), "设置标题")
        status = method(dialog, 3, wintypes.HWND)(dialog, owner)
        if status & 0xffffffff == 0x800704C7:  # ERROR_CANCELLED is a normal user action.
            return None
        check(status, "打开窗口")
        check(method(dialog, 20, ctypes.POINTER(ctypes.c_void_p))(dialog, ctypes.byref(item)), "读取选择")
        check(method(item, 5, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))(
            item, 0x80058000, ctypes.byref(display_name)), "读取文件夹路径")  # SIGDN_FILESYSPATH
        return ctypes.wstring_at(display_name)
    finally:
        if display_name.value:
            ole32.CoTaskMemFree(display_name)
        if item.value:
            method(item, 2)(item)
        if dialog.value:
            method(dialog, 2)(dialog)
        if owner:
            user32.DestroyWindow(owner)
        ole32.CoUninitialize()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    path = _choose_windows_directory()
    print(json.dumps({"path": path} if path else {"cancelled": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
