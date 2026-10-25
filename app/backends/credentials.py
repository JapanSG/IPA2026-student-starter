import os


def router_credentials():
    username = os.getenv("ROUTER_USER") or os.getenv("ROUTER_USERNAME")
    password = os.getenv("ROUTER_PASS") or os.getenv("ROUTER_PASSWORD")
    if not username:
        raise KeyError("ROUTER_USER")
    if not password:
        raise KeyError("ROUTER_PASS")
    return username, password
