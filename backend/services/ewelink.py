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
                    if error_code in (401, 402):
                        logger.warning(f"eWeLink token expired or invalidated (error {error_code} - {resp_json.get('msg')}). Logging in again to refresh...")
                        self.access_token = None
                        return await self.get_all_devices()
                    else:
                        logger.error(f"Device list API error: {error_code} - {resp_json.get('msg')}")
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
            is_int = isinstance(temperature, int) or (isinstance(temperature, str) and "." not in temperature)
            if is_int or temp_val > 100 or temp_val < -100:
                temp_val = temp_val / 100.0
        else:
            temp_val = None

        if humidity is not None:
            hum_val = float(humidity)
            is_int = isinstance(humidity, int) or (isinstance(humidity, str) and "." not in humidity)
            if is_int or hum_val > 100:
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

    @staticmethod
    def is_power_device(params: Dict[str, Any]) -> bool:
        """
        Detect if an eWeLink device is a power monitoring device (like Sonoff POWR320D).
        Power devices report 'power'/'voltage'/'current' instead of 'temperature'/'humidity'.
        """
        return ("power" in params or "voltage" in params or "current" in params)

    @staticmethod
    def is_temp_hum_device(params: Dict[str, Any]) -> bool:
        """Detect if an eWeLink device is a temperature/humidity sensor (like SNZB-02)."""
        return ("temperature" in params or "humidity" in params)

    async def get_power_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch power monitoring data for a specific eWeLink power device (e.g. POWR320D).
        Returns power (W), voltage (V), current (A), switch state, and energy if available.
        """
        thing_list = await self.get_all_devices()
        if not thing_list:
            return None

        target_device = None
        for t in thing_list:
            item_data = t.get("itemData", {})
            if item_data.get("deviceid") == device_id:
                target_device = item_data
                break

        if not target_device:
            logger.warning(f"Power device {device_id} not found in thingList")
            return None

        params_obj = target_device.get("params", {})

        # Power in Watts (POWR320D reports in 0.01 W / cW)
        power = params_obj.get("power")
        if power is not None:
            power_val = float(power)
            if power_val > 1000:
                power_val = round(power_val / 100.0, 2)
        else:
            power_val = 0.0

        # Voltage in Volts (POWR320D reports in 0.01 V / cV)
        voltage = params_obj.get("voltage")
        if voltage is not None:
            voltage_val = float(voltage)
            if voltage_val > 1000:
                voltage_val = round(voltage_val / 100.0, 1)
        else:
            voltage_val = 0.0

        # Current in Amperes (POWR320D reports in mA: 1A = 1000 mA)
        current = params_obj.get("current")
        if current is not None:
            c_float = float(current)
            if c_float > 1000:
                current_val = round(c_float / 1000.0, 2)
            elif c_float > 25:
                current_val = round(c_float / 100.0, 2)
            else:
                current_val = round(c_float, 2)
        else:
            current_val = 0.0

        # Switch state (for POWR320D, check 'switches' array first, then fallback to 'switch' string)
        switch_state = "off"
        if "switches" in params_obj and isinstance(params_obj["switches"], list) and len(params_obj["switches"]) > 0:
            switch_state = str(params_obj["switches"][0].get("switch", "off")).lower()
        elif "switch" in params_obj and params_obj["switch"] is not None:
            switch_state = str(params_obj["switch"]).lower()

        # POWR320D/POW30D reports dayKwh and monthKwh in 0.01 kWh (centi-kWh) units
        day_kwh_raw = params_obj.get("dayKwh") if params_obj.get("dayKwh") is not None else (params_obj.get("oneKwh") if params_obj.get("oneKwh") is not None else params_obj.get("todayKwh"))
        if day_kwh_raw is not None:
            today_energy = round(float(day_kwh_raw) / 100.0, 3)
        else:
            today_energy = 0.0

        month_kwh_raw = params_obj.get("monthKwh")
        if month_kwh_raw is not None:
            month_energy = round(float(month_kwh_raw) / 100.0, 3)
        else:
            month_energy = 0.0
            hundred_days = params_obj.get("hundredDaysKwh")
            if hundred_days and isinstance(hundred_days, str):
                try:
                    days_to_sum = min(30, len(hundred_days) // 6)
                    for i in range(days_to_sum):
                        hex_chunk = hundred_days[i * 6:(i + 1) * 6]
                        if hex_chunk:
                            month_energy += int(hex_chunk, 16) / 100.0
                except Exception:
                    month_energy = 0.0

        is_online = target_device.get("online", False)

        return {
            "power": power_val,
            "voltage": voltage_val,
            "current": current_val,
            "switch": switch_state,
            "today_energy": today_energy,
            "month_energy": month_energy,
            "online": is_online
        }

    async def set_device_switch(self, device_id: str, state: str) -> bool:
        """
        Toggle eWeLink device switch state ('on' or 'off') via WebSocket API.
        """
        import websockets
        if not self.access_token:
            await self.login()

        ws_url = f"wss://{self.region}-pconnect7.coolkit.cc/api/ws"
        auth_payload = {
            "action": "userOnline",
            "at": self.access_token,
            "apikey": self.apikey,
            "appid": self.appid,
            "nonce": self._get_nonce(),
            "ts": int(time.time()),
            "userAgent": "app",
            "sequence": str(int(time.time() * 1000)),
            "version": 8
        }

        try:
            async with websockets.connect(ws_url, open_timeout=8.0, close_timeout=2.0) as ws:
                await ws.send(json.dumps(auth_payload))
                auth_raw = await ws.recv()
                auth_resp = json.loads(auth_raw)
                user_apikey = auth_resp.get("apikey") or self.apikey

                toggle_payload = {
                    "action": "update",
                    "deviceid": device_id,
                    "apikey": user_apikey,
                    "selfApikey": user_apikey,
                    "userAgent": "app",
                    "sequence": str(int(time.time() * 1000)),
                    "params": {
                        "switches": [{"outlet": 0, "switch": state.lower()}],
                        "switch": state.lower()
                    }
                }
                await ws.send(json.dumps(toggle_payload))
                resp_raw = await ws.recv()
                resp_data = json.loads(resp_raw)
                if resp_data.get("error") == 0:
                    logger.info(f"Sent WebSocket toggle command to eWeLink device {device_id} -> {state} SUCCESS")
                    return True
                else:
                    logger.error(f"Failed to toggle eWeLink device {device_id}: {resp_data}")
                    return False
        except Exception as e:
            logger.error(f"Error toggling eWeLink device {device_id} via WebSocket: {e}")
            return False


_GLOBAL_EWELINK_CLIENT = None

async def get_cached_ewelink_client() -> EwelinkClient:
    """
    Returns a singleton EwelinkClient instance with a cached login token.
    Prevents HTTP 429 'invoke too fast' rate limit errors from eWeLink Cloud API.
    """
    global _GLOBAL_EWELINK_CLIENT
    import os

    email = os.getenv("EWELINK_EMAIL") or "grounduppune89@gmail.com"
    password = os.getenv("EWELINK_PASSWORD") or "Groundup"
    region = os.getenv("EWELINK_REGION") or "as"

    if _GLOBAL_EWELINK_CLIENT is None:
        _GLOBAL_EWELINK_CLIENT = EwelinkClient(email, password, region)

    if not _GLOBAL_EWELINK_CLIENT.access_token:
        login_ok = await _GLOBAL_EWELINK_CLIENT.login()
        if not login_ok:
            logger.error("Failed to acquire cached eWeLink login token.")

    return _GLOBAL_EWELINK_CLIENT
