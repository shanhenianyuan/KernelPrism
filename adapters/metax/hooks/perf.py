#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf.py - Metax (MXMACA) adapter v5_profile hook (thin wrapper)

Core logic lives in adapters/_common/mxcagen.py -> run_perf(name='metax')
(platform-parameterized). Output contract aligned with lingspeed:
{timing: {per_iter_ms, std_ms, rounds, samples_used}, detail}
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '_common'))
import mxcagen  # noqa: E402


def main():
    mxcagen.run_perf(name='metax')


if __name__ == '__main__':
    main()
