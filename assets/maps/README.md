# Offline geographic outlines

These generalized outlines are derived from Natural Earth 1:50m administrative
boundaries. Natural Earth geographic data is in the public domain:
https://github.com/nvkelso/natural-earth-vector/blob/master/LICENSE.md

Inputs in that repository's `geojson/` directory:

- `ne_50m_admin_1_states_provinces.geojson`
- `ne_50m_admin_0_countries.geojson`

`tools/build_heatmap_assets.py INPUT_DIRECTORY assets/maps` strips unrelated
attributes and reduces coordinate precision. It does not access the network.
The China view has 34 provincial regions. The world view uses country/region
aggregates; the Chinese provincial regions belong to the CN aggregate.
These are schematic statistical maps, not authoritative boundary documents.
Small islands can be difficult to see; the region/count list remains available.

No member records, location bindings or personal coordinates are included.
