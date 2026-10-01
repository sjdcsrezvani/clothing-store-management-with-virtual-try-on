# Vendored chart engine — offline by construction.
#
# File: echarts.min.js (648,488 bytes minified, ~219 KB gzipped)
# Upstream: Apache ECharts 6.1.0 (Apache-2.0), https://echarts.apache.org
# Custom build — Bar + Line + Pie + Heatmap series; Grid + Tooltip + Legend
# + VisualMap components; SVG renderer (vector-crisp at any DPR or zoom —
# canvas left out deliberately after the retina-blur episode). Everything
# else (geo, graph, gauge, …) stays out.
# Built 2026-10-01 with esbuild 0.28.2 from npm echarts@6.1.0:
#   esbuild echarts-entry.js --bundle --minify --format=iife \
#       --outfile=echarts-custom.min.js
# (entry imports echarts/core, charts, components, renderers as above)
# SHA-256: 78768edd14c9eb122ce611be482227a8133e4f2930679011f9d78f35fbd2c0e6
#
# Why vendored, not CDN: the shop must render charts with no internet —
# outages must never take the analytics page down, and a pinned file
# never changes upstream. The browser loads this from the shop's own
# server exactly like the app's CSS: zero downloads after this commit.
# To upgrade: repeat the build with the new version, replace the file,
# update the hash above.
