#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Compatibility entry point for the read-only Program Lead status consumer."""

from consume_program_status import main


if __name__ == "__main__":
    raise SystemExit(main())
