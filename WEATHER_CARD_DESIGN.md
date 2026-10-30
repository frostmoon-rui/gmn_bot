# Weather card layout

The structured report renders at 1800 pixels wide. Current temperature leads the
header, with precipitation and wind beside it. Large hourly charts precede daily
forecasts and the existing history section. Alerts and two compact source/status
lines remain at the bottom. The separate conversational briefing is unchanged.

- Daily temperature uses floating minimum/maximum bars; any reported apparent
  temperature is a separate diamond on the same Celsius scale.
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

Offline preview with entirely synthetic data:

```powershell
python tools/preview_weather_dashboard.py --output backup/weather-preview.png
```

The preview imports no live bot state, calls no services and reads no user data.
