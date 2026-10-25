"""Environment metadata recorded with every benchmark result."""
import os, platform, subprocess, sys, time


def environment() -> dict:
    def ver(m):
        try:
            return __import__(m).__version__
        except Exception:
            return "not installed"
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except Exception:
        commit, dirty = "unknown", None
    cpu = "unknown"
    try:
        cpu = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception:
        pass
    return {"timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_commit": commit,
            "git_dirty": dirty, "python": sys.version.split()[0], "os": platform.platform(), "cpu": cpu,
            "cpu_count": os.cpu_count(), "numpy": ver("numpy"), "pandas": ver("pandas"), "sklearn": ver("sklearn"),
            "scipy": ver("scipy"), "ortools": ver("ortools")}
