from __future__ import annotations

import multiprocessing as mp
import time

import pytest

from faceless_studio import resources
from faceless_studio.research import fetch_source
from faceless_studio.resources import (
    GpuInfo,
    GpuLockTimeout,
    ResourceUnavailable,
    check_vram,
    gpu_lock,
    looks_like_oom,
)
from faceless_studio.util import FacelessError, ffmpeg_filter_escape, safe_child


def _hold(lock_dir, seconds, ready):
    with gpu_lock(lock_dir, "child", 5):
        ready.set()
        time.sleep(seconds)


def test_gpu_lock_is_exclusive_across_processes(tmp_path):
    ready = mp.Event()
    p = mp.Process(target=_hold, args=(tmp_path, 1.5, ready))
    p.start()
    assert ready.wait(10)
    with pytest.raises(GpuLockTimeout, match="child"), \
            gpu_lock(tmp_path, "parent", timeout_s=0.3, poll_s=0.05):
        pass
    p.join()
    with gpu_lock(tmp_path, "parent", timeout_s=1):
        pass


def test_gpu_lock_released_when_holder_dies(tmp_path):
    ready = mp.Event()
    p = mp.Process(target=_hold, args=(tmp_path, 60, ready))
    p.start()
    assert ready.wait(10)
    p.kill()
    p.join()
    with gpu_lock(tmp_path, "after-crash", timeout_s=2):
        pass


def test_gpu_lock_reentrant(tmp_path):
    with gpu_lock(tmp_path, "a", 1), gpu_lock(tmp_path, "a", 1):
        pass


def test_vram_headroom(monkeypatch):
    monkeypatch.setattr(resources, "query_gpu", lambda: GpuInfo("g", 6141, 2000, 4141, "d"))
    check_vram(3000, 768, "x")
    with pytest.raises(ResourceUnavailable, match="VRAM"):
        check_vram(3500, 768, "x")
    monkeypatch.setattr(resources, "query_gpu", lambda: None)
    with pytest.raises(ResourceUnavailable, match="no NVIDIA"):
        check_vram(1, 1, "x")


def test_oom_detection():
    assert looks_like_oom("RuntimeError: CUDA error: out of memory")
    assert looks_like_oom("torch.OutOfMemoryError: ...")
    assert not looks_like_oom("connection refused")


def test_safe_child_blocks_traversal(tmp_path):
    assert safe_child(tmp_path, "a", "b.txt") == tmp_path.resolve() / "a" / "b.txt"
    with pytest.raises(FacelessError):
        safe_child(tmp_path, "..", "etc", "passwd")


def test_filter_escape():
    # Matches the two-level example in the ffmpeg-filters documentation.
    assert ffmpeg_filter_escape("/a:b,c'd[e]") == "/a\\\\:b\\,c\\\\\\'d\\[e\\]"


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost/", "file:///etc/passwd",
                                 "http://10.0.0.1/", "ftp://example.org/"])
def test_fetch_refuses_private_and_non_http(url):
    with pytest.raises(FacelessError):
        fetch_source(url, "S1")
