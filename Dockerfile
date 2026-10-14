# Simulator/production container for the label printer app.
#
# WHY THIS EXISTS: the reference deployment is a Raspberry Pi with the real Brother QL on USB
# (scripts/brother-setup.sh). This image runs the SAME code as a container: map the device in
# (--device /dev/usb/lp0, see docker-compose.example.yml) and it drives the printer like the Pi
# does; leave it out and it serves the animated simulator at /sim. Nothing to configure either
# way - the presence of the device decides (see hardware_vorhanden() in app.py).
#
# fonts-dejavu-core: create_label_image() loads DejaVuSans(-Bold).ttf from the Debian font path;
# without the package Pillow silently falls back to a bitmap font and every label looks wrong.

FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/printer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app.py api.py simulator.py session_jwt.py ./
COPY templates/ templates/

# NO PRINTER_SIM HERE ON PURPOSE. The variable is an override now, in both directions, and a
# baked-in "0" would force the hardware path in every container that has no printer - every
# print answering 503 like a broken device. Unset means: let the device decide.
EXPOSE 5000

# Not root (guild Karte 1535, D2). uid 1013 is unused on the NAS that runs printer-int and is not
# 1000 (the interactive account there). The app writes exactly one file, runtime.txt (the active
# event), so only that file belongs to the user; the code stays root-owned and read-only for it.
# Mounted files (api_tokens.txt, .env) must be readable by uid 1013 - an unreadable token file
# disables the API (every call 401), it never lets anyone in.
# The printer device: group_add "lp" in the compose file (see docker-compose.example.yml).
RUN useradd -u 1013 -U -M -d /nonexistent -s /usr/sbin/nologin printer \
 && touch runtime.txt && chown printer:printer runtime.txt
# Karte 1466: printer hat kein Home (/nonexistent); gunicorn legt seinen Control-Socket unter $HOME an
# und meldete sonst bei jedem Start "Permission denied: /nonexistent".
ENV HOME=/tmp
USER printer

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5000/session-status || exit 1

# EXACTLY one worker: the API session and the simulator buffer live in process memory (by
# design, see api.py/simulator.py) - a second worker would see neither. Threads carry the
# concurrency instead. --access-logfile - keeps requests visible in docker logs.
CMD ["gunicorn", "-b", "0.0.0.0:5000", "--workers", "1", "--threads", "8", \
     "--access-logfile", "-", "app:app"]
