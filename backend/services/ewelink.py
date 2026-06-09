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
    def __init__(self, email: Optional[str] = None, password: Optional[str] = None, region: str = "as", access_token: Optional[str] = None):
        self.email = email
        self.password = password
        self.region = region
        # Custom developer credentials from eWeLink Console
        self.appid = "k7I2Cjco0LzajauACqXz60hKC5DeyJWd"
        self.appsecret = "1f5PyuItgiBT3oisfzGJiKFtyMSoPDxK"
        
        self.base_url = f"https://{region}-apia.coolkit.cc"
        self.access_token = access_token
        self.apikey: Optional[str] = None

    def _get_nonce(self) -> str:
        return "".join(random.choices(string.ascii_letters + string.digits, k=8))

    async def exchange_code(self, code: str, redirect_uri: str) -> Optional[dict]:
        """
        Exchange OAuth2 authorization code for access and refresh tokens.
        """
        url = f"{self.base_url}/v2/user/oauth/token"
        nonce = self._get_nonce()
        
        payload = {
            "code": code,
            "grantType": "authorization_code",
            "redirectUri": redirect_uri
        }
        
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
                logger.info(f"Attempting token exchange at {url}")
                response = await client.post(url, content=serialized.encode('utf-8'), headers=headers, timeout=15)
                if response.status_code != 200:
                    logger.error(f"Token exchange HTTP error: {response.status_code} - {response.text}")
                    return None
                
                resp_json = response.json()
                if resp_json.get("error", 0) != 0:
                    logger.error(f"Token exchange API error: {resp_json.get('error')} - {resp_json.get('msg')}")
                    return None
                
                return resp_json.get("data") # Contains at (accessToken), rt (refreshToken), etc.
        except Exception as e:
            logger.error(f"Exception during token exchange: {str(e)}")
            return None

    async def refresh_tokens(self, refresh_token: str) -> Optional[dict]:
        """
        Refresh access token using a refresh token.
        """
        url = f"{self.base_url}/v2/user/refresh"
        nonce = self._get_nonce()
        
        payload = {
            "rt": refresh_token
        }
        
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
                logger.info(f"Attempting token refresh at {url}")
                response = await client.post(url, content=serialized.encode('utf-8'), headers=headers, timeout=15)
                if response.status_code != 200:
                    logger.error(f"Token refresh HTTP error: {response.status_code} - {response.text}")
                    return None
                
                resp_json = response.json()
                if resp_json.get("error", 0) != 0:
                    logger.error(f"Token refresh API error: {resp_json.get('error')} - {resp_json.get('msg')}")
                    return None
                
                return resp_json.get("data") # Contains at (accessToken), rt (refreshToken), etc.
        except Exception as e:
            logger.error(f"Exception during token refresh: {str(e)}")
            return None

    async def get_all_devices(self) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch all devices/things registered under the eWeLink account.
        """
        if not self.access_token:
            logger.error("No access token present in EwelinkClient")
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
                    return None

                thing_list = resp_json.get("data", {}).get("thingList", [])
                return thing_list
        except Exception as e:
            logger.error(f"Exception fetching eWeLink devices: {str(e)}")
            return None

    async def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch telemetry (temperature, humidity, battery) of a specific device from the list of things.
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
        
        # Extract readings
        temperature = params_obj.get("temperature")
        humidity = params_obj.get("humidity")
        battery = params_obj.get("battery")
        
        # Clean temperature and humidity readings
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
