import asyncio
import json
from tapo import ApiClient

async def main():
    print("Connecting to Tapo P110 plug...")
    client = ApiClient("grounduppune89@gmail.com", "Groundup@89")
    device = await client.p110("192.168.0.115")
    
    print("\n--- Device Info ---")
    info = await device.get_device_info()
    print("Device Info properties:")
    print(info.to_dict() if hasattr(info, "to_dict") else info)
    
    print("\n--- Energy Usage ---")
    energy = await device.get_energy_usage()
    print("Energy Usage properties:")
    print(energy.to_dict() if hasattr(energy, "to_dict") else energy)
    for attr in dir(energy):
        if not attr.startswith('_'):
            try:
                val = getattr(energy, attr)
                print(f"  {attr}: {val} (type: {type(val)})")
            except Exception as e:
                print(f"  {attr}: Error reading: {e}")

if __name__ == "__main__":
    asyncio.run(main())
