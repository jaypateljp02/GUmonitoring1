
import sys
import os
import asyncio
import json

sys.path.append("C:/GroundUp/ground-up-monitoring")
from backend.services.ewelink import EwelinkClient

async def test_ewelink():
    email = os.getenv("EWELINK_EMAIL", "grounduppune89@gmail.com")
    pwd = os.getenv("EWELINK_PASSWORD", "Groundup")
    reg = os.getenv("EWELINK_REGION", "as")
    
    print(f"Logging in to eWeLink as {email}...")
    c = EwelinkClient(email, pwd, reg)
    await c.login()
    
    print("Fetching all devices...")
    thing_list = await c.get_all_devices()
    
    if thing_list:
        print("Found devices. Looking for 'Replacing white Fridge' plug (10029128b2)...")
        for t in thing_list:
            item = t.get("itemData", {})
            did = item.get("deviceid")
            if did == "10029128b2":
                print("\n--- RAW EWELINK API DATA FOR 10029128b2 ---")
                print(json.dumps(item, indent=2))
                
                params = item.get("params", {})
                sw_state = "off"
                if "switches" in params and isinstance(params["switches"], list) and len(params["switches"]) > 0:
                    sw_state = str(params["switches"][0].get("switch", "off")).lower()
                elif "switch" in params and params["switch"] is not None:
                    sw_state = str(params["switch"]).lower()
                    
                print(f"\nParsed switch_state: {sw_state}")
                print(f"Online status: {item.get('online', False)}")
                break

asyncio.run(test_ewelink())
