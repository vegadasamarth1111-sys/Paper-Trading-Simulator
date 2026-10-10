#!/usr/bin/env python
"""Django management command entry point for Paper Trading Simulator."""

import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Ensure it's installed and "
            "available on your PYTHONPATH. Did you activate your virtualenv?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
