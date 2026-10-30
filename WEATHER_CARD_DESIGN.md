# Weather card layout

The structured report renders at 1800 pixels wide. Current temperature leads the
header, with precipitation and wind beside it. Large hourly charts precede daily
forecasts and the existing history section. Alerts and two compact source/status
lines remain at the bottom. The separate conversational briefing is unchanged.

- Daily temperature uses vertical floating minimum/maximum columns, with dates
  along the horizontal axis. Reported apparent temperature remains a separate
  diamond on the Celsius scale. Hourly temperature uses the same vertical layout:
  actual/feels-like readings keep their own endpoint colours and names, while
  single readings remain points. These pairs are not invented hourly extrema.
- Rainfall columns and precipitation/thunderstorm probability lines share time
  positions, with independently labelled millimetre and percentage axes. Rain,
  showers and total precipitation are not summed or stacked as independent totals.
- Wind and gusts share a speed scale; bearing samples remain discrete and are
  never connected across north. Humidity/cloud cover and the two pressure series
  share like-unit panels. UV and visibility use separate labelled axes.
- Missing samples remain missing, real time gaps break lines, and repeated DST
  hours use distinct instants. One-sided daily temperatures remain single points.
- Legends and supporting text wrap for translated labels; sparse reports omit
  empty chart sections. The existing single-topic chart commands remain available.
- Time/date headers, temperature endpoints, current readings and section titles
  use bold faces. Local vector condition icons accompany the temperature columns;
  explicit provider day/night flags distinguish sun and moon. Missing conditions
  use a neutral mark. Icons never infer clear weather from zero or missing rain.
- Hourly condition codes remain paired with the provider supplying their weather
  description. Raster supersampling improves glyphs, rounded borders and column
  edges without interpolating, smoothing or replacing any measurement. Final
  width stays 1800 pixels; long reports use a bounded working raster.

Offline preview with entirely synthetic data:

```powershell
python tools/preview_weather_dashboard.py --output backup/weather-preview.png
```

The preview imports no live bot state, calls no services and reads no user data.
