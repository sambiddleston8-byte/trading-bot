#!/bin/zsh

set -eu

cd "$(dirname "$0")/.."

ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
if [ -z "$ip" ]; then
  ip="$(ipconfig getifaddr en1 2>/dev/null || true)"
fi

if [ -n "$ip" ]; then
  echo "Open http://${ip}:8501 on an iPhone connected to this same Wi-Fi network."
else
  echo "The app will start now. Find this Mac's Wi-Fi IP address and open http://<that-address>:8501 on the iPhone."
fi

exec .venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501
