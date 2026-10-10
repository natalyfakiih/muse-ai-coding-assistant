#!/bin/sh
# What the container runs on boot.
#
# A free host wipes its disk on every deploy and on every wake-up from idle, so
# the database arrives empty. A demo link that opens on "No data yet — run
# python -m app.seed" is worse than no link at all, so both data steps run here
# before the server starts.
set -e

# Additive and matched on title/entry point: it adds what is missing and never
# overwrites a student's work, so running it on every boot is safe.
python -m app.seed

# Fills the instructor dashboard with a cohort's worth of finished work, with
# no LLM calls — every string in app/demo.py is written down, not generated.
#
# It exits non-zero when the database already has specification work in it.
# On a restart with a surviving disk that is the normal case, not a failure, so
# it must not take the server down with it. `set -e` above still catches a real
# failure in the seed step, which is the one that has to work.
python -m app.demo || echo "muse: demo data already present, leaving it alone"

# exec, so uvicorn becomes PID 1 and receives the host's shutdown signal
# directly. Without it the shell swallows SIGTERM and the platform waits out
# its grace period before killing the container on every single deploy.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
