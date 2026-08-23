#!/usr/bin/env bash
set -euo pipefail
base64 -d next_gen_s8p_mars_sync_packet_20260619.tar.gz.b64 > next_gen_s8p_mars_sync_packet_20260619.tar.gz
tar -xzf next_gen_s8p_mars_sync_packet_20260619.tar.gz
bash next_gen_s8p_mars_sync_packet_20260619/INSTALL_ON_MARS.sh
