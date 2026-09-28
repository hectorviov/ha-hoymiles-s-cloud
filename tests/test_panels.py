import struct, datetime
from unittest.mock import MagicMock, patch
from custom_components.hoymiles_nimbus.micro import HoymilesMicroCoordinator, HoymilesPanelSensor, HoymilesMicroSensor, MICRO_SENSORS, PANEL_SENSORS
from custom_components.hoymiles_nimbus.micro_data import parse_count_by_day, parse_module_count_by_day

def varint(n):
    out=b''
    while True:
        b=n&0x7f; n>>=7
        out+=bytes([b|0x80 if n else b])
        if not n: return out
def ld(f,b): return varint((f<<3)|2)+varint(len(b))+b
def vi(f,n): return varint(f<<3)+varint(n)
def dbl(vals): return b''.join(struct.pack('<d',v) for v in vals)
MID=2000001
TIMES=['11:50','11:55','12:00']
def micro_payload(date):
    m=b''.join(ld(1,t.encode()) for t in TIMES)
    for q,v in {'MI_POWER':[1900,0,2026.1],'MI_NET_V':[269.8,264.4,267.4],'MI_NET_RATE':[60,60,60.02],'MI_TEMPERATURE':[63,62.8,63.7]}.items():
        m+=ld(2, ld(1,q.encode())+ld(2,dbl(v))+vi(3,MID))
    return m+ld(3,date.encode())
def module_payload(date):
    m=b''.join(ld(1,t.encode()) for t in TIMES)
    for port in (1,2,3,4):
        for q,v in {'MODULE_POWER':[480,0,505+port],'MODULE_V':[40.1,0,40.5],'MODULE_I':[11.9,0,12.5]}.items():
            m+=ld(2, ld(1,q.encode())+ld(2,dbl(v))+vi(3,MID)+vi(4,port))
    return m+ld(3,date.encode())

def client_for(date):
    c=MagicMock()
    c.scan_interval_s=300
    c.select_by_page.return_value=[{"id":1000001,"name":"Test Plant"}]
    c.select_by_station.return_value={"list":[{"id":MID,"sn":"1164A0000001","model_no":"HMS-2000-4T"}]}
    c.micro_find.return_value={"layout_list":[{"mi_id":MID,"port":p,"x":0,"y":3+p} for p in (1,2,3,4)]}
    c.micro_count_by_day.side_effect=lambda sid,d,ids: parse_count_by_day(micro_payload(date), ids)
    c.module_count_by_day.side_effect=lambda sid,d,mid,ports: parse_module_count_by_day(module_payload(date))
    return c

async def _values(hass, now, date):
    with patch("custom_components.hoymiles_nimbus.micro.dt_util.now", return_value=now):
        coord=HoymilesMicroCoordinator(hass, client_for(date), None)
        await coord.async_refresh()
    assert coord.last_update_success, coord.last_exception
    micro=coord.micros[0]
    panels={(p["port"],k): HoymilesPanelSensor(coord, micro, p, k) for p in micro.ports for k in PANEL_SENSORS}
    micro_s={d.key: HoymilesMicroSensor(coord, micro, d).native_value for d in MICRO_SENSORS}
    return panels, micro_s

async def test_midday(hass):
    now=datetime.datetime(2026,9,28,12,3)
    panels, m = await _values(hass, now, "2026-09-28")
    p=panels[(1,"power")]
    print(p.name, p.unique_id, p.native_value, p.extra_state_attributes)
    assert p.unique_id=="hoymiles_nimbus_module_1164A0000001-1_power"
    assert p.name=="Test Plant Panel 1164A0000001-1 Power"
    assert [panels[(i,"power")].native_value for i in (1,2,3,4)]==[506,507,508,509]
    assert panels[(2,"voltage")].native_value==40.5 and panels[(2,"current")].native_value==12.5
    assert m["grid_voltage"]==267.4 and m["dropouts_today"]==1

async def test_night_is_zero_not_garbage(hass):
    now=datetime.datetime(2026,9,28,23,36)   # last slot 12:00 is hours old
    panels, m = await _values(hass, now, "2026-09-28")
    assert all(panels[(i,"power")].native_value==0.0 for i in (1,2,3,4))
    assert panels[(1,"voltage")].native_value is None
    assert m["ac_power"]==0.0 and m["grid_voltage"] is None
    assert m["grid_voltage_max_today"]==269.8

async def test_yesterdays_data_is_stale(hass):
    now=datetime.datetime(2026,9,29,0,10)
    panels, m = await _values(hass, now, "2026-09-28")
    assert panels[(3,"power")].native_value==0.0
