#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compile.py - Lingspeed LUCA adapter v3_compile hook (thin wrapper)

Core logic lives in adapters/_common/mxcagen.py -> run_compile() (platform-parameterized,
shared by lingspeed/metax). This file only forwards the pipeline's stdin JSON payload to
run_compile while keeping the output contract unchanged:
  BuildArtifact {build_ok, detail, build_log_path, artifacts:[{kind,ref}],
                 toolchain, env_snapshot, duration}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_compile()


if __name__ == '__main__':
    main()
