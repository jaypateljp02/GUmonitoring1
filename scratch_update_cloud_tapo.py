"""Update tapo_ip on the PRODUCTION cloud database via the API."""
import httpx

CLOUD_URL = "https://gu-monitoring.initiativesewafoundation.com"

updates = {
    "a4b002884e": "192.168.0.109",   # Black Fridge (was .102, now .109)
    "a4b002898f": "192.168.0.101",   # Miso room (was .117, now .101)
    # a4b0028d6e stays at .100 (already working)
}

for device_id, new_ip in updates.items():
    payload = {
        "temp_tapo_ip": new_ip,
        "hum_tapo_ip": new_ip,
    }
    url = f"{CLOUD_URL}/sensors/device/{device_id}/thresholds"
    print(f"Updating {device_id} -> tapo_ip={new_ip}")
    r = httpx.put(url, json=payload, timeout=15.0)
    print(f"  Status: {r.status_code} | Response: {r.text}")

print("\nDone!")
