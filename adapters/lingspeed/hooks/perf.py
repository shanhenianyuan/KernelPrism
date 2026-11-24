#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf.py - Lingspeed LUCA adapter v5_profile hook (thin wrapper)

Core logic lives in adapters/_common/mxcagen.py -> run_perf() (platform-parameterized,
shared by lingspeed/metax). Output contract unchanged:
{timing: {per_iter_ms, std_ms, rounds, samples_used}, detail}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_perf()


if __name__ == '__main__':
    main()
