import time
import random
import string
import hmac
import hashlib
import base64
import json
import logging
from typing import Optional, List, Dict, Any
import httpx

logger = logging.getLogger(__name__)

class EwelinkClient:
    def __init__(self, email: str, password: str, region: str = "as"):
        self.email = email
        self.password = password
        self.region = region
        # Official app credentials from SonoffLAN
        self.appid = "4s1FXKC9FaGfoqXhmXSJneb3qcm1gOak"
        self.appsecret = "oKvCM06gvwkRbfetd6qWRrbC3rFrbIpV"
        
        self.base_url = f"https://{region}-apia.coolkit.cc"
        self.access_token: Optional[str] = None
        self.apikey: Optional[str] = None

    def _get_nonce(self) -> str:
        return "".join(random.choices(string.ascii_letters + string.digits, k=8))

    async def login(self) -> bool:
        """
        Log in to eWeLink cloud and retrieve accessToken and apikey.
        """
        url = f"{self.base_url}/v2/user/login"
        nonce = self._get_nonce()
        
        payload = {
            "email": self.email,
            "password": self.password,
            "appid": self.appid,
            "ts": int(time.time()),
            "version": 8,
            "nonce": nonce,
            "countryCode": "+91"
        }
        
        # Serialize ONCE — use this exact bytes for both signing AND sending
        serialized = json.dumps(payload, separators=(',', ':'))
        sig_bytes = hmac.new(self.appsecret.encode('utf-8'), serialized.encode('utf-8'), hashlib.sha256).digest()
        signature = base64.b64encode(sig_bytes).decode('utf-8')

        headers = {
            "X-CK-Appid": self.appid,
            "X-CK-Nonce": nonce,
            "Authorization": f"Sign {signature}",
            "Content-Type": "application/json; charset=utf-8"
        }

        try:
            async with httpx.AsyncClient() as client:
                logger.info(f"Attempting eWeLink login for {self.email} to {url}")
                response = await client.post(url, content=serialized.encode('utf-8'), headers=headers, timeout=15)
                if response.status_code != 200:
                    logger.error(f"Login HTTP error: {response.status_code} - {response.text}")
                    return False
                
                resp_json = response.json()
                error_code = resp_json.get("error", 0)
                if error_code != 0:
                    logger.error(f"Login API error: {error_code} - {resp_json.get('msg')}")
                    if error_code == 10004 and "region" in resp_json.get("data", {}):
                        new_region = resp_json["data"]["region"]
                        logger.info(f"Redirected to region: {new_region}")
                        self.region = new_region
                        self.base_url = f"https://{new_region}-apia.coolkit.cc"
                        return await self.login()
                    return False
                
                data = resp_json.get("data", {})
                self.access_token = data.get("at")
                self.apikey = data.get("user", {}).get("apikey")
                logger.info("Login SUCCESS! Token obtained.")
                return True
        except Exception as e:
            logger.error(f"Exception during eWeLink login: {str(e)}")
            return False

    async def get_all_devices(self) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch all devices/things registered under the eWeLink account.
        """
        if not self.access_token:
            if not await self.login():
                return None

        url = f"{self.base_url}/v2/device/thing"
        headers = {
            "X-CK-Appid": self.appid,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=15)
                if response.status_code != 200:
                    logger.error(f"Failed to fetch devices: {response.status_code} - {response.text}")
                    return None

                resp_json = response.json()
                error_code = resp_json.get("error", 0)
                if error_code != 0:
                    logger.error(f"Device list API error: {error_code} - {resp_json.get('msg')}")
                    if error_code in (401, 402):
                        self.access_token = None
                        return await self.get_all_devices()
                    return None

                thing_list = resp_json.get("data", {}).get("thingList", [])
                return thing_list
        except Exception as e:
            logger.error(f"Exception fetching eWeLink devices: {str(e)}")
            return None

    async def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch telemetry parameters of a specific device from the list.
        """
        thing_list = await self.get_all_devices()
        if not thing_list:
            return None

        # Look for the target device
        target_device = None
        for t in thing_list:
            item_data = t.get("itemData", {})
            if item_data.get("deviceid") == device_id:
                target_device = item_data
                break
                
        if not target_device:
            logger.warning(f"Device {device_id} not found in thingList")
            return None

        params_obj = target_device.get("params", {})
        temperature = params_obj.get("temperature")
        humidity = params_obj.get("humidity")
        battery = params_obj.get("battery")
        
        if temperature is not None:
            temp_val = float(temperature)
            if temp_val > 100 or temp_val < -100:
                temp_val = temp_val / 100.0
        else:
            temp_val = None

        if humidity is not None:
            hum_val = float(humidity)
            if hum_val > 100:
                hum_val = hum_val / 100.0
        else:
            hum_val = None
            
        if battery is not None:
            bat_val = float(battery)
        else:
            bat_val = 100.0 # Default fallback

        return {
            "temperature": temp_val,
            "humidity": hum_val,
            "battery": bat_val
        }
