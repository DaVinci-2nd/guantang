#!/bin/sh
cd "$(dirname "$0")"
python3 server.py || echo "启动失败，请确认已安装依赖：pip install -e ."
