#!/usr/bin/env bash
# OTP 도보·자동차 그래프용 OSM: Geofabrik 간토 추출본(약 520MB)을 받아
# 도쿄 23구 + 하네다·나리타 공항을 포함하는 범위로 자른다. © OpenStreetMap contributors, ODbL.
# 자르기는 osmium-tool(Debian 패키지)을 컨테이너 안에서 실행한다.
# MSYS_NO_PATHCONV: Windows Git Bash가 컨테이너 경로(/d)를 Windows 경로로 바꾸지 않게 한다.
set -euo pipefail
cd "$(dirname "$0")"
BBOX="139.55,35.50,140.45,35.82"  # 서,남,동,북
if [ ! -f kanto-latest.osm.pbf ]; then
  curl -fsSL --retry 3 -o kanto-latest.osm.pbf.part https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
  mv kanto-latest.osm.pbf.part kanto-latest.osm.pbf
fi
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/d" -w /d debian:stable-slim sh -c \
  "apt-get update -qq && apt-get install -y -qq osmium-tool >/dev/null && \
   osmium extract --overwrite -s smart -b $BBOX kanto-latest.osm.pbf -o tokyo.osm.pbf"
rm -f kanto-latest.osm.pbf
ls -l tokyo.osm.pbf
