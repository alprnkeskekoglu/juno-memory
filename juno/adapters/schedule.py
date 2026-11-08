import datetime
import os
import shutil
import subprocess
import sys
from typing import Any

from juno import config, paths
from juno.adapters import checks, fs, stalerun
from juno.core import schedule as core
from juno.core.errors import Refused

PASSED_ENV = ("JUNO_CONFIG", "JUNO_VAULT", "JUNO_STATE", "JUNO_REPO_ROOTS", "JUNO_CARD_CLAUDE")


def platform(cfg: config.Config) -> str:
    return cfg.env.get("JUNO_SCHEDULE_PLATFORM") or sys.platform


def home(cfg: config.Config) -> str:
    return paths.home(cfg.env)


def plist_path(cfg: config.Config) -> str:
    return os.path.join(home(cfg), "Library", "LaunchAgents", f"{core.LABEL}.plist")


def unit_dir(cfg: config.Config) -> str:
    xdg = cfg.env.get("XDG_CONFIG_HOME", "")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.join(home(cfg), ".config")
    return os.path.join(base, "systemd", "user")


def unit_paths(cfg: config.Config) -> tuple[str, str]:
    base = os.path.join(unit_dir(cfg), core.UNIT)
    return base + ".service", base + ".timer"


def log_path(cfg: config.Config) -> str:
    return os.path.join(stalerun.jobs_dir(cfg), "stale-weekly.log")


def job_env(cfg: config.Config) -> dict[str, str]:
    launch = cfg.get_str("handoff", "launch_path").replace("~", home(cfg))
    env = {"HOME": home(cfg), "PATH": launch}
    env.update({k: cfg.env[k] for k in PASSED_ENV if cfg.env.get(k)})
    return env


def window(cfg: config.Config) -> tuple[str, int, int]:
    day, start, end = (
        cfg.get_str("stale", "weekday"),
        cfg.get_int("stale", "start_hour"),
        cfg.get_int("stale", "end_hour"),
    )
    problem = core.window_problem(day, start, end)
    if problem:
        raise Refused(problem)
    return day, start, end


def argv(cfg: config.Config) -> list[str]:
    python = cfg.get_str("runtime", "python") or sys.executable
    root = cfg.get_str("runtime", "plugin_root") or paths.plugin_root()
    return core.command(python, root)


def tool(cfg: config.Config, name: str) -> str:
    return cfg.env.get(f"JUNO_{name.upper()}") or name


def call(cfg: config.Config, name: str, *args: str) -> tuple[int, str]:
    try:
        done = subprocess.run(
            [tool(cfg, name), *args],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env=dict(cfg.env),
        )
    except OSError, subprocess.SubprocessError:
        return 127, f"{name} did not run"
    return done.returncode, (done.stdout + done.stderr).strip()


def domain() -> str:
    return f"gui/{os.getuid()}"


def install(cfg: config.Config) -> list[str]:
    off = checks.enabled_refusal(cfg)
    if off:
        raise Refused(f"{off}; set [stale] auto_fix = true in juno.toml first")
    day, start, end = window(cfg)
    fs.ensure_private_dir(stalerun.jobs_dir(cfg))
    if platform(cfg) == "darwin":
        return install_launchd(cfg, day, start, end)
    return install_systemd(cfg, day, start, end)


def install_launchd(cfg: config.Config, day: str, start: int, end: int) -> list[str]:
    path = plist_path(cfg)
    data = core.plist(argv(cfg), job_env(cfg), log_path(cfg), day, start, end)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fs.atomic_write(path, data, 0o644)
    call(cfg, "launchctl", "bootout", f"{domain()}/{core.LABEL}")
    code, out = call(cfg, "launchctl", "bootstrap", domain(), path)
    if code != 0:
        raise Refused(f"launchctl bootstrap failed ({out or code}); the plist is at {path}")
    return [f"schedule: loaded {core.LABEL} ({path})", fire_line(day, start, end)]


def install_systemd(cfg: config.Config, day: str, start: int, end: int) -> list[str]:
    command = argv(cfg)
    if shutil.which(tool(cfg, "systemctl"), path=cfg.env.get("PATH")) is None:
        line = core.cron_line(command, day, start, end, log_path(cfg))
        return ["schedule: systemd is not available; add this line to your crontab:", line]
    service, timer = unit_paths(cfg)
    os.makedirs(os.path.dirname(service), exist_ok=True)
    fs.atomic_write(service, core.service(command, job_env(cfg), log_path(cfg)).encode(), 0o644)
    fs.atomic_write(timer, core.timer(day, start, end).encode(), 0o644)
    call(cfg, "systemctl", "--user", "daemon-reload")
    code, out = call(cfg, "systemctl", "--user", "enable", "--now", f"{core.UNIT}.timer")
    if code != 0:
        raise Refused(f"systemctl enable failed ({out or code}); the units are in {unit_dir(cfg)}")
    return [f"schedule: enabled {core.UNIT}.timer ({timer})", fire_line(day, start, end)]


def fire_line(day: str, start: int, end: int) -> str:
    return (
        f"schedule: runs {day} every hour from {start:02d}:00 to {end:02d}:00 until a week is "
        "done; log in the state dir under jobs/stale-weekly"
    )


def remove(cfg: config.Config) -> list[str]:
    out: list[str] = []
    if platform(cfg) == "darwin":
        path = plist_path(cfg)
        call(cfg, "launchctl", "bootout", f"{domain()}/{core.LABEL}")
        if os.path.exists(path):
            os.unlink(path)
            out.append(f"schedule: removed {path}")
    else:
        call(cfg, "systemctl", "--user", "disable", "--now", f"{core.UNIT}.timer")
        for path in unit_paths(cfg):
            if os.path.exists(path):
                os.unlink(path)
                out.append(f"schedule: removed {path}")
        call(cfg, "systemctl", "--user", "daemon-reload")
    return out or ["schedule: nothing installed"]


def installed(cfg: config.Config) -> bool:
    if platform(cfg) == "darwin":
        return os.path.exists(plist_path(cfg))
    return os.path.exists(unit_paths(cfg)[1])


def loaded(cfg: config.Config) -> bool:
    if platform(cfg) == "darwin":
        return call(cfg, "launchctl", "print", f"{domain()}/{core.LABEL}")[0] == 0
    return call(cfg, "systemctl", "--user", "is-enabled", f"{core.UNIT}.timer")[0] == 0


def status(cfg: config.Config, now: datetime.datetime | None = None) -> dict[str, Any]:
    day, start, end = (
        cfg.get_str("stale", "weekday"),
        cfg.get_int("stale", "start_hour"),
        cfg.get_int("stale", "end_hour"),
    )
    is_installed = installed(cfg)
    last = stalerun.last_row(cfg, ("ok", "failed", "refused", "budget", "deferred"))
    found: dict[str, Any] = {
        "installed": is_installed,
        "loaded": is_installed and loaded(cfg),
        "auto_fix": cfg.get_bool("stale", "auto_fix"),
        "last": None
        if last is None
        else {k: last.get(k) for k in ("run", "day", "status", "fixed", "archived", "reason")},
    }
    if is_installed and not core.window_problem(day, start, end):
        stamp = now or datetime.datetime.now().astimezone()
        found["next"] = core.next_fire(stamp, day, start, end).isoformat(timespec="minutes")
    return found
