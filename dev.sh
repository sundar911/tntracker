#!/usr/bin/env bash
set -e

LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")
PORT=${1:-8000}

echo ""
echo "  Local:  http://localhost:$PORT"
echo "  Phone:  http://$LOCAL_IP:$PORT"
echo ""

cd "$(dirname "$0")/backend"
python manage.py runserver "0.0.0.0:$PORT"
