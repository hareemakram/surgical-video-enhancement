#!/usr/bin/env bash
# Download the openly licensed (CC BY 2.0) laparoscopy clip credited in ATTRIBUTION.md
# and convert it to an MP4 the pipeline and the demo can read.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="A-gallbladder-torsion-presenting-as-acute-cholecystitis-in-an-elderly-woman-A-case-report-1752-1947-5-588-S1.ogv"
URL="https://commons.wikimedia.org/wiki/Special:FilePath/${NAME}"
mkdir -p data/real
if [ ! -s data/real/source.ogv ]; then
  echo "Downloading ${NAME} (CC BY 2.0, Boer et al. 2011) ..."
  curl -fL --retry 3 -A "surgical-video-enhancement-demo/1.0" -o data/real/source.ogv "$URL"
fi
ffmpeg -y -loglevel error -i data/real/source.ogv -an -c:v libx264 -pix_fmt yuv420p -crf 18 data/real/laparoscopy.mp4
echo "Wrote data/real/laparoscopy.mp4"
