"""Run the remote daemon on 127.0.0.1 only; no secret-bearing CLI arguments.

--config is an optional owner-only JSON file with root, port and Worker resource
limits. Explicit --root/--port take precedence. Cache/TMP overrides are applied
before FastAPI, uvicorn or any future inference adapter is imported. Starting
before model download is supported and reports runtime_ready=false.
"""
import argparse
import json
import os
from pathlib import Path
import stat


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Argparse normally echoes unknown option values (possibly credentials).
        raise SystemExit("invalid remote arguments; use --help")


def configuration(argv=None):
    parser = _PrivateArgumentParser(description=__doc__)
    parser.add_argument("--root")
    parser.add_argument("--config")
    parser.add_argument("--port", type=int)
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    config = {}
    if args.config:
        fd = os.open(Path(args.config).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
                raise ValueError("configuration must be a bounded owner-only regular file")
            config = json.load(stream)
        allowed = {"root", "port", "queue_limit", "max_output_bytes", "min_free_bytes", "retention_seconds", "retained_bytes", "max_runtime_seconds"}
        if type(config) is not dict or set(config) - allowed:
            raise ValueError("unknown configuration option")
    root = args.root or config.pop("root", "~/MinimaxH3Server")
    config.pop("root", None)
    port = args.port if args.port is not None else config.pop("port", 8765)
    config.pop("port", None)
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(root, str):
        raise ValueError("invalid root or port")
    if args.profile is not None:
        from h3server.backends import get_profile
        get_profile(args.profile)
        config['profile'] = args.profile
    return root, port, config


def main(argv=None):
    # Remote execution is Linux/POSIX. This does not limit the Windows controller.
    if os.name != "posix":
        raise SystemExit("the remote daemon requires a POSIX server")
    os.umask(0o077)
    try:
        root, port, limits = configuration(argv)
        from h3server.paths import AppPaths
        paths = AppPaths(root)
        for directory in paths.directories():
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ.update(paths.cache_environment())
        from h3server.remote.app import create_app, load_token
        from h3server.remote.worker import Worker
        from h3server.store import Store
        import uvicorn
        store = Store(paths.state / "remote.sqlite")
        worker = Worker(store, paths, **limits)
        app = create_app(store, worker, token=load_token(paths))
    except (OSError, ValueError):
        # Config/IO exception strings can contain sensitive paths or values.
        raise SystemExit("remote configuration failed; check owner-only files and limits") from None
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="critical")


if __name__ == "__main__":
    main()
