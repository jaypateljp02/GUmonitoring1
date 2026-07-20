import httpx

PROD_URL = "https://gu-monitoring.initiativesewafoundation.com"

try:
    print("Fetching /rooms from PROD...")
    r = httpx.get(f"{PROD_URL}/rooms", timeout=15.0)
    print(f"Status: {r.status_code}")
    rooms = r.json()
    print(f"Total Rooms: {len(rooms)}")
    
    # Take first room and fetch device telemetry
    first_room = rooms[0]
    sensor = first_room.get("sensors", [])[0]
    device_id = sensor.get("device_id")
    print(f"\nFetching /sensors/device/{device_id}/telemetry?days=1 from PROD...")
    r = httpx.get(f"{PROD_URL}/sensors/device/{device_id}/telemetry?days=1", timeout=15.0)
    print(f"Status: {r.status_code}")
    print(r.json())
except Exception as e:
    print(f"Error: {e}")
