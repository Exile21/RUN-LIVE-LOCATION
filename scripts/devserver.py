"""Local development server wrapper.

Some macOS builds stall in the standard library's reverse-DNS lookup while
binding Django's development server. Short-circuiting `getfqdn` during local
development avoids that wait. This script is never used in production.
"""

import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "runlive.settings")

socket.getfqdn = lambda name="": name or "localhost"

from django.core.management import execute_from_command_line  # noqa: E402

if __name__ == "__main__":
    address = sys.argv[1:] or ["127.0.0.1:8000"]
    execute_from_command_line(["manage.py", "runserver", *address, "--noreload"])
