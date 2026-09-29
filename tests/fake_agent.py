"""Pretend to be `tempo-agent`: record the argv we were launched with, then exit 0."""

import json
import os
import sys

out = os.environ.get("FAKE_AGENT_LOG")
if out:
    with open(out, "a") as f:
        f.write(
            json.dumps({"argv": sys.argv[1:], "cap": os.environ.get("TEMPO_AGENT_MAX_COST_USD")})
            + "\n"
        )
