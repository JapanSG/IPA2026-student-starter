import json
import os
import re
import shutil
import subprocess
from ipaddress import IPv4Interface
from pathlib import Path


PLAYBOOK = (
    Path(__file__).resolve().parents[2]
    / "ansible"
    / "apply_interface.yml"
)


def _error(result):
    return {"status": "error", "result": result}


def _error_from_output(output):
    normalized = output.lower()
    if any(
        marker in normalized
        for marker in (
            "authentication failed",
            "authenticationfailure",
            "authentication failure",
            "permission denied",
            "invalid username",
            "invalid password",
            "incorrect password",
            "unauthorized",
        )
    ):
        return _error("authentication_failed")
    if any(
        marker in normalized
        for marker in (
            "unreachable",
            "connection refused",
            "connection timed out",
            "timed out",
            "no route to host",
            "network is unreachable",
            "unable to connect",
            "connection failure",
        )
    ):
        return _error("connection_failed")
    return _error("backend_failed")


def _run_real_playbook(router, variables):
    executable = shutil.which("ansible-playbook")
    if executable is None:
        raise RuntimeError("ansible-playbook executable is unavailable")

    completed = subprocess.run(
        [
            executable,
            "--inventory",
            f"{router},",
            "--extra-vars",
            json.dumps(variables),
            str(PLAYBOOK),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    if completed.returncode != 0:
        raise _PlaybookFailure(
            f"{completed.stdout}\n{completed.stderr}"
        )


class _PlaybookFailure(RuntimeError):
    pass


def apply_interface(router, desired_interface, run_playbook=None):
    """Apply complete desired interface state using Ansible."""
    try:
        interface = IPv4Interface(desired_interface["ipv4"])
        match = re.fullmatch(r"Loopback(\d+)", desired_interface["name"])
        if (
            match is None
            or desired_interface["admin_state"] not in ("up", "down")
        ):
            return _error("backend_failed")

        variables = {
            "router": router,
            "interface_name": desired_interface["name"],
            "description": desired_interface["description"],
            "ipv4_address": str(interface.ip),
            "netmask": str(interface.network.netmask),
            "shutdown": desired_interface["admin_state"] == "down",
        }

        if run_playbook is not None:
            run_playbook(variables)
        else:
            _run_real_playbook(router, variables)
        return {"status": "ok", "result": "applied"}
    except PermissionError:
        return _error("authentication_failed")
    except (ConnectionError, TimeoutError, subprocess.TimeoutExpired):
        return _error("connection_failed")
    except _PlaybookFailure as error:
        return _error_from_output(str(error))
    except (RuntimeError, KeyError, ValueError, TypeError, OSError):
        return _error("backend_failed")
