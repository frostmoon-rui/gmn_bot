"""Render synthetic weather cards offline; no bot, API, cache or personal data."""
import argparse
from datetime import datetime, timedelta
import math
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from weather_delivery import render_report
from weather_report import format_weather


def fixture():
    start=datetime(2026,10,30)
    hours=[]
    for index in range(24):
        temperature=7+6*math.sin((index-7)*math.pi/14)
        rain=round(max(0,3.5-abs(index-15))*.7,2)
        hours.append({'time':(start+timedelta(hours=index)).isoformat()+'+08:00',
            'temperature_2m':round(temperature,1),'apparent_temperature':round(temperature-3,1),
            'precipitation':rain,'rain':round(rain*.8,2),'showers':round(rain*.2,2),
            'precipitation_probability':min(95,round(rain*30+10)),
            'wind_speed_10m':round(18+10*math.sin(index/4)**2,1),
            'wind_gusts_10m':round(31+20*math.sin(index/4)**2,1),
            'wind_direction_10m':(260+index*6)%360,'relative_humidity_2m':round(50+20*math.sin(index/5)**2),
            'cloud_cover':round(15+75*math.sin(index/6)**2),'pressure_msl':round(1016+4*math.cos(index/7),1),
            'surface_pressure_hpa':round(999+4*math.cos(index/7),1),
            'dew_point_2m':round(temperature-8,1),'heat_index':round(temperature-.3,1),
            'wind_chill':round(temperature-3,1),'uv_index':round(max(0,4*math.sin((index-6)/12*math.pi)),1),
            'visibility_km':round(16-rain*3,1),'thunderstorm_probability':round(rain*5), 'snowfall':0})
    days=[]
    for index,(low,high) in enumerate([(2,14),(0,10),(-3,7),(-5,4),(-2,8),(1,12),(3,15)]):
        days.append({'date':(start+timedelta(days=index)).date().isoformat(),
            'minTemperature':{'degrees':low},'maxTemperature':{'degrees':high},
            'apparent_temperature_max':high-3,'precipitation_sum':[5.2,8.6,1.4,0,0,.2,0][index],
            'probability':[70,85,40,10,5,20,5][index],'wind_speed_10m_max':20+index*2,
            'wind_gusts_10m_max':38+index*3,'uv_index_max':max(1,4-index*.3),
            'humidity':52+index*2,'cloudCover':max(0,70-index*8)})
    return {'local_date':'2026-10-30','timezone':'Asia/Shanghai','report_time':'2026-10-30T09:30+08:00',
        'temp':7,'feels_like':3,'min_temp':2,'max_temp':14,'desc':'Partly cloudy',
        'precipitation_mm':.4,'probability':65,'wind_kmph':28,'wind_gust_kmph':54,
        'humidity':72,'uv':2,'pressure_hpa':1019,'visibility_km':12,
        'hours':hours,'days':days,'sources':['Google Weather','Open-Meteo model'],
        'provider_status':{'Google Weather':'acquired','Open-Meteo model':'acquired'}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--tomorrow',action='store_true')
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(render_report(format_weather(fixture(),'tomorrow' if args.tomorrow else 'today')))
    print(args.output.resolve())
