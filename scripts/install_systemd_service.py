import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import grp
    import pwd
except ImportError:
    grp = None
    pwd = None


DEFAULT_SERVICE_NAME = "papermoney-radio-monitor"
DEFAULT_DESCRIPTION = "PaperMoneyRadioMonitor service"


def infer_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def infer_python_executable(repo_root: Path) -> str:
    candidates = [
        repo_root / ".venv" / "bin" / "python",
        repo_root / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    raise FileNotFoundError("Could not determine a Python executable for the service.")


def infer_account_group(user: str) -> str:
    if grp is None or pwd is None:
        return user
    try:
        return grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name
    except KeyError as exc:
        raise KeyError(f"Unable to infer primary group for user '{user}'.") from exc


def render_unit(template_path: Path, replacements) -> str:
    template = template_path.read_text(encoding="utf-8")
    return template.format(**replacements)


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_command(args: list[str], *, check: bool = True) -> None:
    print(f"+ {' '.join(args)}")
    subprocess.run(args, check=check)


def main() -> int:
    repo_root = infer_repo_root()
    parser = argparse.ArgumentParser(
        description="Generate or install a systemd service for PaperMoneyRadioMonitor."
    )
    parser.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--user", default=os.environ.get("USER", "pi"))
    parser.add_argument("--group")
    parser.add_argument("--working-directory", default=str(repo_root))
    parser.add_argument("--env-file", default=str((repo_root / ".env").resolve()))
    parser.add_argument("--python-executable", default=infer_python_executable(repo_root))
    parser.add_argument("--monitor-script", default=str((repo_root / "monitor.py").resolve()))
    parser.add_argument(
        "--output",
        help="Write the generated unit file here. Defaults to <repo>/systemd/<service-name>.service.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Copy the generated unit file into /etc/systemd/system.",
    )
    parser.add_argument(
        "--enable-now",
        action="store_true",
        help="Run systemctl enable --now after installation. This opts into boot-time startup.",
    )
    args = parser.parse_args()

    template_path = repo_root / "systemd" / "papermoney-radio-monitor.service.template"
    if not template_path.exists():
        raise FileNotFoundError(f"Missing unit template: {template_path}")

    group = args.group or infer_account_group(args.user)
    output_path = Path(args.output) if args.output else repo_root / "systemd" / f"{args.service_name}.service"
    replacements = {
        "description": args.description,
        "user": args.user,
        "group": group,
        "working_directory": str(Path(args.working_directory).resolve()),
        "env_file": str(Path(args.env_file).resolve()),
        "python_executable": str(Path(args.python_executable).resolve()),
        "monitor_script": str(Path(args.monitor_script).resolve()),
    }
    unit_text = render_unit(template_path, replacements)
    write_file(output_path, unit_text)
    print(f"Wrote unit file to {output_path}")

    if args.install:
        target_path = Path("/etc/systemd/system") / f"{args.service_name}.service"
        if os.geteuid() != 0:
            raise PermissionError("Installing into /etc/systemd/system requires root privileges.")
        shutil.copyfile(output_path, target_path)
        print(f"Installed unit file to {target_path}")
        run_command(["systemctl", "daemon-reload"])
        if args.enable_now:
            run_command(["systemctl", "enable", "--now", args.service_name])
        else:
            print(f"Run manually when needed: sudo systemctl start {args.service_name}")
    else:
        print("Generation only. To install on the Pi, rerun with --install under sudo.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
