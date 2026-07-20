import httpx
r = httpx.get('http://localhost:8003/rooms')
rooms = r.json()
for rm in rooms:
    name = rm.get("name", "?")
    sensors = rm.get("sensors", [])
    for s in sensors:
        stype = s.get("type", "?")
        did = s.get("device_id", "?")
        tapo_ip = s.get("tapo_ip", "")
        print(f"{name:30s} | {stype:12s} | device_id={did} | tapo_ip={tapo_ip}")
