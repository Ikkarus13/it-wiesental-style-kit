#!/usr/bin/env python3
"""Startskript: python3 filefenster.py [ORDNER …]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filefenster.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
