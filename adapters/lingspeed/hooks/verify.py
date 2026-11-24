#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.py - Lingspeed LUCA adapter v4_verify hook (thin wrapper)

Core logic lives in adapters/_common/mxcagen.py -> run_verify() (platform-parameterized,
shared by lingspeed/metax). Output contract unchanged:
{max_abs_err, max_rel_err, pass, detail}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_verify()


if __name__ == '__main__':
    main()
