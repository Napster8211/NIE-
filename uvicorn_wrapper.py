#!/usr/bin/env python3
"""Uvicorn execution wrapper that handles unexpanded shell variables in container runtimes.

Railway or cloud environments that execute start commands without shell expansion
pass the literal string '$PORT' to --port. This wrapper replaces '$PORT' or '${PORT}'
with the parsed integer from os.environ['PORT'] before handing off to uvicorn.main.
"""
import os
import re
import sys

port_val = os.environ.get("PORT", "8000").strip()
if not port_val.isdigit():
    port_val = "8000"

for i, arg in enumerate(sys.argv):
    if arg in ("$PORT", "${PORT}"):
        sys.argv[i] = port_val

from uvicorn.main import main

if __name__ == "__main__":
    sys.argv[0] = re.sub(r"(-script\.pyw|\.exe)?$", "", sys.argv[0])
    sys.exit(main())
