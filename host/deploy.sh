#!/bin/sh
# Deploy my.gritmeter.app to the Pi: copy the code, rebuild the image there and restart (data/ and .env stay on the Pi).
#   host/deploy.sh          code only
#   host/deploy.sh --osm    also copy the OSM tile cache (data/osm/) after rebuilding it on the Mac
# PI (default pi4.local) and DIR (default /opt/gritmeter) can be overridden from the environment.
# host/'s files land at the top of DIR (next to data/ and .env), gritmeter/ and site/ beside them.
set -eu
PI=${PI:-pi4.local}
DIR=${DIR:-/opt/gritmeter}
cd "$(dirname "$0")/.."

rsync -az --delete \
  --exclude .env --exclude data/ --exclude __pycache__/ --exclude .DS_Store \
  host/ gritmeter site "$PI:$DIR/"
if [ "${1:-}" = "--osm" ]; then
  rsync -az data/osm/ "$PI:$DIR/data/osm/"
fi
ssh "$PI" "cd $DIR && docker compose up -d --build && docker compose ps"
