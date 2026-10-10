# MUSE, containerised for a public demo.
#
# One image serves everything. FastAPI mounts `frontend/` at /static and serves
# the pages itself, so there is no second web server, no build step and no
# bundler — what runs in the container is what runs on your laptop.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them on every build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Most hosts pass the port in at runtime; 8000 is the fallback for `docker run`.
ENV PORT=8000
EXPOSE 8000

# The reflection engine runs student code in a subprocess under POSIX resource
# limits (RLIMIT_CPU, RLIMIT_AS, RLIMIT_NPROC). Those are real here because the
# base image is Linux — on Windows only the wall-clock timeout applies, so the
# sandbox is actually stronger in this container than it is in development.

# Invoked through `sh` rather than as `./start.sh`: the file is authored on
# Windows, where git does not track an executable bit, so the image would
# otherwise fail to boot with "permission denied" on a file that looks fine.
CMD ["sh", "start.sh"]
