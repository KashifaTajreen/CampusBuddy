"""Sample owned process-tree RSS and whole-device VRAM; no extra packages."""
import ctypes
import os
from pathlib import Path
import subprocess
import threading
import time


def rss_tree(roots):
    if os.name!="nt":
        parents={}
        for path in Path("/proc").glob("[0-9]*/status"):
            try:
                fields=dict(line.split(":",1) for line in path.read_text().splitlines() if ":" in line)
                parents[int(path.parent.name)]=(int(fields["PPid"]),int(fields.get("VmRSS","0 kB").split()[0])*1024)
            except (OSError,ValueError,KeyError):
                pass
    else:
        from ctypes import wintypes as w
        class Entry(ctypes.Structure):
            _fields_=[("size",w.DWORD),("usage",w.DWORD),("pid",w.DWORD),("heap",ctypes.c_size_t),
                ("module",w.DWORD),("threads",w.DWORD),("parent",w.DWORD),("priority",w.LONG),
                ("flags",w.DWORD),("name",w.WCHAR*260)]
        class Memory(ctypes.Structure):
            _fields_=[("size",w.DWORD),("faults",w.DWORD),*[(k,ctypes.c_size_t) for k in
                ("peak_rss","rss","peak_paged","paged","peak_nonpaged","nonpaged","pagefile","peak_pagefile")]]
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD]
        kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
        kernel.OpenProcess.restype=w.HANDLE
        kernel.CloseHandle.argtypes=[w.HANDLE]
        kernel.Process32FirstW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes=[w.HANDLE,ctypes.POINTER(Entry)]
        psapi=ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes=[w.HANDLE,ctypes.POINTER(Memory),w.DWORD]
        snapshot=kernel.CreateToolhelp32Snapshot(2,0)
        if snapshot==ctypes.c_void_p(-1).value:
            return None
        entry=Entry()
        entry.size=ctypes.sizeof(entry)
        parents={}
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            parents[entry.pid]=(entry.parent,0)
            ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
        kernel.CloseHandle(snapshot)
    owned=set(roots)
    while True:
        additions={pid for pid,(parent,_) in parents.items() if parent in owned}-owned
        if not additions:
            break
        owned.update(additions)
    total=0
    found=False
    for pid in owned:
        if os.name=="nt":
            handle=kernel.OpenProcess(0x410,False,pid)
            if not handle:
                continue
            memory=Memory()
            memory.size=ctypes.sizeof(memory)
            if psapi.GetProcessMemoryInfo(handle,ctypes.byref(memory),memory.size):
                total+=memory.rss
                found=True
            kernel.CloseHandle(handle)
        elif pid in parents:
            total+=parents[pid][1]
            found=True
    return total/2**20 if found else None


class ResourceMonitor:
    def __init__(self):
        self.roots={os.getpid()}
        self.stages={}
        self.current="setup"
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.sample,daemon=True)
    def begin(self,label):
        with self.lock:
            self.current=label
            self.stages[label]=dict(sampled_peak_owned_rss_mib=None,sampled_peak_device_vram_mib=None,samples=0)
    def sample(self):
        last_gpu=0
        vram=None
        while not self.stop.is_set():
            with self.lock:
                label=self.current
            ram=rss_tree(set(self.roots))
            fresh_gpu=False
            if time.monotonic()-last_gpu>2:
                try:
                    result=subprocess.run(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"],
                        capture_output=True,text=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                    vram=float(result.stdout.strip().splitlines()[0]) if result.returncode==0 else None
                except (OSError,subprocess.SubprocessError,ValueError,IndexError):
                    vram=None
                last_gpu=time.monotonic()
                fresh_gpu=True
            with self.lock:
                if self.current!=label:
                    continue
                stage=self.stages.get(self.current)
                if stage is not None:
                    stage["samples"]+=1
                    for key,value in (("sampled_peak_owned_rss_mib",ram),("sampled_peak_device_vram_mib",vram if fresh_gpu else None)):
                        if value is not None:
                            stage[key]=max(stage[key] or 0,value)
            self.stop.wait(.5)
    def __enter__(self):
        self.begin("setup")
        self.thread.start()
        return self
    def __exit__(self,*_):
        self.stop.set()
        self.thread.join(timeout=7)
