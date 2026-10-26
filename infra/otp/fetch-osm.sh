#!/usr/bin/env bash
# P0 헬스체크용 도쿄 OSM 추출본(BBBike, 약 90MB)을 받는다. © OpenStreetMap contributors, ODbL.
# P1에서 간토 추출본(Geofabrik) + GTFS 그래프로 바꾼다.
set -euo pipefail
cd "$(dirname "$0")"
curl -fsSL --retry 3 -o tokyo.osm.pbf.part https://download.bbbike.org/osm/bbbike/Tokyo/Tokyo.osm.pbf
mv tokyo.osm.pbf.part tokyo.osm.pbf
ls -l tokyo.osm.pbf
