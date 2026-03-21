#!/bin/bash
cd "$(dirname "$0")"
venv/bin/python scripts/evaluate.py "$@"
