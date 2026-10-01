# Vendored chart engine — offline by construction.
#
# File: echarts.min.js (581,0xx bytes minified, ~201 KB gzipped)
# Upstream: Apache ECharts 6.1.0 (Apache-2.0), https://echarts.apache.org
# Custom build — Bar + Line + Pie series; Grid + Tooltip + Legend
# components; Canvas renderer. Everything else (geo, graph, gauge,
# SVG renderer, …) stays out.
# Built 2026-10-01 with esbuild 0.28.2 from npm echarts@6.1.0:
#   esbuild echarts-entry.js --bundle --minify --format=iife \
#       --outfile=echarts-custom.min.js
# (entry imports echarts/core, charts, components, renderers as above)
# SHA-256: 1721e4ac013da32154db099997116758943984ba461699e9a77a5734a7a81609
#
# Why vendored, not CDN: the shop must render charts with no internet —
# outages must never take the analytics page down, and a pinned file
# never changes upstream. The browser loads this from the shop's own
# server exactly like the app's CSS: zero downloads after this commit.
# To upgrade: repeat the build with the new version, replace the file,
# update the hash above.
