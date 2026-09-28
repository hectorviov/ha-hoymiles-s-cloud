import struct
from unittest.mock import patch
from custom_components.hoymiles_nimbus.hoymiles_client import HoymilesClient
from custom_components.hoymiles_nimbus.micro_data import latest_module_values
def varint(n):
    out=b''
    while True:
        b=n&0x7f; n>>=7
        out+=bytes([b|0x80 if n else b])
        if not n: return out
def ld(f,b): return varint((f<<3)|2)+varint(len(b))+b
def vi(f,n): return varint(f<<3)+varint(n)
VALS={'MODULE_POWER':250.5,'MODULE_V':41.2,'MODULE_I':6.03}
def fake_post(uri, payload=None, headers=None, response_type='json', **kw):
    if len(payload["quota"])>1: return b""          # real server behaviour
    q=payload["quota"][0]
    m=ld(1,b'09:00')+ld(1,b'09:05')
    for e in payload["mi_list"]:
        m+=ld(2, ld(1,q.encode())+ld(2,struct.pack('<d',0)+struct.pack('<d',VALS[q]+e["port"]))+vi(3,e["id"])+vi(4,e["port"]))
    return m+ld(3,b'2026-09-28')
def test_one_quota_per_request():
    c=HoymilesClient("u","p","https://x/"); c.token="t"
    with patch.object(c,"_post_request",side_effect=fake_post) as m:
        day=c.module_count_by_day(1,"2026-09-28",2000001,[1,2,3,4])
    assert m.call_count==3
    v=latest_module_values(day,2000001,2)
    assert (v["power"],v["voltage"],v["current"],v["time"])==(252.5,43.2,8.03,"09:05"), v

def fake_post_lagging_current(uri, payload=None, headers=None, response_type='json', **kw):
    q=payload["quota"][0]
    times=['09:00','09:05'] if q!='MODULE_I' else ['09:00']          # current one slot behind
    m=b''.join(ld(1,t.encode()) for t in times)
    for e in payload["mi_list"]:
        vals={'MODULE_POWER':[300,343.2],'MODULE_V':[39.0,39.4],'MODULE_I':[7.7]}[q]
        m+=ld(2, ld(1,q.encode())+ld(2,b''.join(struct.pack('<d',v) for v in vals))+vi(3,e["id"])+vi(4,e["port"]))
    return m+ld(3,b'2026-09-28')

def test_current_calculated_when_missing_for_latest_slot():
    c=HoymilesClient("u","p","https://x/"); c.token="t"
    with patch.object(c,"_post_request",side_effect=fake_post_lagging_current):
        day=c.module_count_by_day(1,"2026-09-28",2000001,[1])
    v=latest_module_values(day,2000001,1)
    assert v["power"]==343.2 and v["voltage"]==39.4 and v["current"]==8.71 and v["current_calculated"], v

def test_current_from_api_when_present():
    c=HoymilesClient("u","p","https://x/"); c.token="t"
    with patch.object(c,"_post_request",side_effect=fake_post):
        day=c.module_count_by_day(1,"2026-09-28",2000001,[1,2,3,4])
    v=latest_module_values(day,2000001,1)
    assert v["current"]==7.03 and not v["current_calculated"], v
