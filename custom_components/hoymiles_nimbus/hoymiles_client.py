import base64
import binascii
import datetime
import requests
import threading
import yaml
import logging
import hashlib
from cachetools import TTLCache, cached

# Handle imports for both standalone and Home Assistant contexts
try:
    from .micro_data import ALL_QUOTAS, MODULE_QUOTAS, parse_count_by_day, parse_module_count_by_day
except ImportError:
    from micro_data import ALL_QUOTAS, MODULE_QUOTAS, parse_count_by_day, parse_module_count_by_day

_LOGGER = logging.getLogger(__name__)
_CACHE_LOCK = threading.RLock()


def _redact(headers):
    """Copy of the headers that is safe to log (no session token)."""
    return {k: ("***" if k.lower() == "authorization" else v) for k, v in (headers or {}).items()}


class HoymilesClient:
    """
    Client for interacting with Hoymiles S-Cloud API.
    
    This class provides methods organized into different categories:
    - Authentication: login, token management
    - HTTP helpers: internal request methods
    - Data fetching: retrieve information from API
    - Control operations: send commands to devices
    - System mapping: build system hierarchy
    - Utilities: helper functions
    """
    
    def __init__(self, username, password, base_url):
        """Initialize the Hoymiles client with credentials and base URL."""
        _LOGGER.debug("Initializing HoymilesClient")
        
        self.username = username
        self.password = password
        self.base_url = base_url
        
        # API endpoint URIs
        self.uris = {
            "login": "iam/pub/0/auth/login",
            "user_info": "iam/api/1/user/me",
            "count_station_data": "pvm-data/api/0/station/data/count_station_real_data",
            "find": "pvm/api/0/station/find",
            "select_by_station": "pvm/api/0/dev/micro/select_by_station",
            "micro_find": "pvm/api/0/dev/micro/find",
            "select_all_arrays": "pvm/api/0/dev/array_v3/select_all",
            "down_station_day_data": "pvm-data/api/0/station/down_station_day_data",
            "select_device_of_tree": "pvm/api/0/station/select_device_of_tree",
            "micro_count_by_day": "pvm-data/api/0/micro/data/count_by_day",
            "module_count_by_day": "pvm-data/api/0/module/data/count_by_day",
        }
        
        self.token = None
        self.cache = TTLCache(maxsize=100, ttl=300)
        # Refresh interval shared by all sensors (seconds); set from the options.
        self.scan_interval_s = 300
        self._station_cache = {}

    # ============================================================================
    # HTTP HELPER METHODS
    # ============================================================================


    def _post_request(self, uri, payload=None, headers=None, use_auth=True, binary=False, response_type='json'):
        """Helper method to make POST requests."""
        url = f"{self.base_url}{uri}"
        if headers is None:
            headers = {"Content-Type": "application/json"}
        for k, v in getattr(self, "_extra_headers", {}).items():
            headers.setdefault(k, v)
        if use_auth:
            if self.token:
                headers["Authorization"] = self.token
            else:
                raise Exception("Token is not set. Please authenticate first.")

        _LOGGER.debug(f"POST Request URL: {url}")
        _LOGGER.debug(f"POST Request Payload: {payload}")
        _LOGGER.debug("POST Request Headers: %s", _redact(headers))
        
        response = requests.post(url, json=payload, headers=headers)
        
        try:
            _LOGGER.debug(f"Response Status Code: {response.status_code}")
            _LOGGER.debug(f"Response Headers: {response.headers}")
            response.raise_for_status()
            
            # Attempt to parse the response as JSON
            try:
                if response_type == 'raw':
                    return response.content
                response_data = response.json()
                logging.debug(f"Response JSON: {response_data}")
                _LOGGER.debug("API Response: %s - Success", response.status_code)
                return response_data
            except ValueError:
                logging.error("Failed to parse response as JSON")
                _LOGGER.debug("API Response: %s - Failed to parse JSON", response.status_code)
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Request failed: {e}")
            _LOGGER.warning("API Response: Request failed - %s", str(e))
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            _LOGGER.warning("API Response: Unexpected error - %s", str(e))
            raise
        
    def _put_request(self, uri, payload=None, headers=None):
        """Helper method to make PUT requests."""
        url = f"{self.base_url}{uri}"
        if headers is None:
            headers = {"Content-Type": "application/json"}
        for k, v in getattr(self, "_extra_headers", {}).items():
            headers.setdefault(k, v)
        if self.token:
            headers["Authorization"] = self.token
        else:
            raise Exception("Token is not set. Please authenticate first.")

        _LOGGER.debug(f"PUT Request URL: {url}")
        _LOGGER.debug(f"PUT Request Payload: {payload}")
        _LOGGER.debug("PUT Request Headers: %s", _redact(headers))

        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        
        # Attempt to parse the response as JSON
        try:
            response_data = response.json()
            _LOGGER.debug(f"Response JSON: {response_data}")
            _LOGGER.debug("API Response: %s - Success", response.status_code)
            return response_data
        except ValueError:
            _LOGGER.error("Failed to parse response as JSON")
            _LOGGER.debug("API Response: %s - Failed to parse JSON", response.status_code)
            return None

    # ============================================================================
    # AUTHENTICATION METHODS
    # ============================================================================

    def get_password_hash(self):
      password = self.password.encode('utf-8')
      passwordHash = hashlib.md5(password)
      return passwordHash.hexdigest()

    # Client identities accepted by the v3 login. "web" mimics the S-Cloud
    # website; "installer" mimics the S-Miles Installer app (some owner accounts
    # created by an installer only accept this one).
    _AUTH_PROFILES = {
        "web": {"User-Agent": "HomeAssistant-HoymilesNimbus"},
        "installer": {
            "User-Agent": "S-Miles Installer/3.7.1",
            "App-Version": "3.7.1",
            "X-App-Version": "3.7.1",
            "X-Client-Type": "mobile",
        },
    }

    def _auth_post(self, path, payload, extra_headers):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(extra_headers)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        try:
            return response.json()
        except ValueError:
            return {"status": str(response.status_code), "message": response.text[:200]}

    @staticmethod
    def _unwrap_pre_insp(resp):
        if isinstance(resp, dict) and ("status" in resp or "data" in resp):
            data = resp.get("data")
            return str(resp.get("status")), resp.get("message"), data if isinstance(data, dict) else {}
        if isinstance(resp, dict) and any(k in resp for k in ("a", "n", "u")):
            return "0", "success", resp
        return None, (resp or {}).get("message") if isinstance(resp, dict) else None, {}

    @staticmethod
    def _decode_salt(value):
        value = value.strip()
        try:
            if len(value) % 2 == 0:
                return bytes.fromhex(value)
        except ValueError:
            pass
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return value.encode()

    def _login_v3(self, profile):
        """Browser/app login: pre-insp (salt + nonce) then login with a credential hash.

        Returns (token, error_message).
        """
        extra = self._AUTH_PROFILES[profile]
        pre = self._auth_post("iam/pub/3/auth/pre-insp", {"u": self.username}, extra)
        status, message, data = self._unwrap_pre_insp(pre)
        if status not in (None, "0") or not data.get("n"):
            return None, f"pre-insp: status={status} message={message}"

        salt = data.get("a")
        if salt:
            try:
                from argon2.low_level import Type, hash_secret_raw
            except ImportError:
                return None, "account needs Argon2 login but argon2-cffi is not installed"
            raw = hash_secret_raw(
                secret=self.password.encode(), salt=self._decode_salt(salt),
                time_cost=3, memory_cost=32768, parallelism=1, hash_len=32, type=Type.ID,
            )
            candidates = [("argon2", raw.hex())]
        else:
            md5_hex = hashlib.md5(self.password.encode()).hexdigest()
            sha = hashlib.sha256(self.password.encode())
            candidates = [
                ("md5.sha256b64", f"{md5_hex}.{base64.b64encode(sha.digest()).decode()}"),
                ("sha256hex", sha.hexdigest()),
            ]

        nonce = data["n"]
        last = None
        for i, (variant, ch) in enumerate(candidates):
            if i > 0:  # every attempt needs a fresh nonce
                status, message, data = self._unwrap_pre_insp(
                    self._auth_post("iam/pub/3/auth/pre-insp", {"u": self.username}, extra))
                if not data.get("n"):
                    break
                nonce = data["n"]
            resp = self._auth_post("iam/pub/3/auth/login", {"u": self.username, "ch": ch, "n": nonce}, extra)
            token = (resp.get("data") or {}).get("token") if isinstance(resp, dict) else None
            if str(resp.get("status")) == "0" and token:
                _LOGGER.info("Hoymiles login OK (v3, %s profile, %s hash)", profile, variant)
                return token, None
            last = f"login {variant}: status={resp.get('status')} message={resp.get('message')}"
        return None, last

    def _login_v0(self):
        resp = self._auth_post("iam/pub/0/auth/login",
                               {"user_name": self.username, "password": self.get_password_hash()},
                               self._AUTH_PROFILES["web"])
        token = (resp.get("data") or {}).get("token") if isinstance(resp, dict) else None
        if str(resp.get("status")) == "0" and token:
            _LOGGER.info("Hoymiles login OK (legacy v0)")
            return token, None
        return None, f"status={resp.get('status')} message={resp.get('message')}"

    def login(self):
        """Authenticate with Hoymiles S-Cloud and retrieve a token.

        Tries the current v3 login (web, then installer identity) and falls back
        to the legacy v0 MD5 login.
        """
        _LOGGER.debug("Logging into Hoymiles S-Cloud for user: %s", self.username)
        errors = []
        for profile in ("web", "installer"):
            try:
                token, err = self._login_v3(profile)
            except Exception as ex:  # noqa: BLE001
                token, err = None, str(ex)
            if token:
                self.token = token
                self._extra_headers = dict(self._AUTH_PROFILES[profile])
                return True
            errors.append(f"v3/{profile}: {err}")
        try:
            token, err = self._login_v0()
        except Exception as ex:  # noqa: BLE001
            token, err = None, str(ex)
        if token:
            self.token = token
            self._extra_headers = dict(self._AUTH_PROFILES["web"])
            return True
        errors.append(f"v0: {err}")
        summary = " | ".join(errors)
        _LOGGER.error("Hoymiles login failed: %s", summary)
        raise Exception(f"Login failed: {summary}")

    # ============================================================================
    # DATA FETCHING METHODS
    # ============================================================================

    @cached(cache=TTLCache(maxsize=100, ttl=300), lock=_CACHE_LOCK)
    def select_by_station(self, station_id):
        """Select microinverters by station ID."""
        payload = {
            "sid": station_id,
            "page": 1,
            "page_size": 1000,
            "show_warn": 0
        }
        response = self._post_request(self.uris['select_by_station'], payload=payload)

        return response.get("data", {})
    
    @cached(cache=TTLCache(maxsize=100, ttl=300), lock=_CACHE_LOCK)
    def micro_find(self, micro_id, station_id):
        """Find a microinverter by its ID."""
        payload = {
            "id": micro_id,
            "sid": station_id,
        }
        response = self._post_request(self.uris['micro_find'], payload=payload)
        return response.get('data', {})

    @cached(cache=TTLCache(maxsize=100, ttl=300), lock=_CACHE_LOCK)
    def get_user_info(self):
        """Retrieve user information from Hoymiles S-Cloud. [UNUSED]"""
        return self._post_request(self.uris['user_info'])

    @cached(cache=TTLCache(maxsize=100, ttl=300), lock=_CACHE_LOCK)
    def select_by_page(self, type):
        uri_map = {
            "station":  "pvm/api/0/station/select_by_page",
            "dtu":      "pvm/api/0/dev/dtu/select_by_page",
            "micro":    "pvm/api/0/dev/micro/select_by_page",
            # Add more types here if needed
        }

        # Get the URI based on the type
        uri = uri_map.get(type)
        if not uri:
            raise ValueError(f"Invalid type for select_by_page: {type}")

        payload = {
            "page": 1,
            "page_size": 100,
        }
        response = self._post_request(uri, payload=payload)

        if not isinstance(response, dict):
            _LOGGER.warning(
                "Unexpected response from Hoymiles station API: %r",
                response,
            )
            return []
        
        data = response.get("data")
        
        if not isinstance(data, dict):
            _LOGGER.warning(
                "Unexpected station data format from Hoymiles API: %r",
                data,
            )
            return []
        
        return data.get("list", [])
        
    
    def count_station_real_data(self, id):
        """Station totals (power, today/total energy). Cached for the refresh interval."""
        import time
        with _CACHE_LOCK:
            hit = self._station_cache.get(id)
            if hit and time.monotonic() - hit[0] < self.scan_interval_s:
                return hit[1]
        _LOGGER.debug(f"Getting count of station real data for ID: {id}")
        data = self._post_request(self.uris['count_station_data'], payload={"sid": id})
        with _CACHE_LOCK:
            self._station_cache[id] = (time.monotonic(), data)
        return data

    @cached(cache=TTLCache(maxsize=100, ttl=300), lock=_CACHE_LOCK)
    def findStation(self, sid):
        """Find a station by its ID."""
        payload = {
            "id": sid,
        }
        response = self._post_request(self.uris['find'], payload=payload)
        return response.get('data', {})

    def micro_count_by_day(self, sid, date, micro_ids, quotas=None):
        """Per-microinverter day series: AC power, grid voltage, grid frequency, temperature.

        Returns a MicroDaySeries (see micro_data.py). Retries once with a fresh
        login if the token has expired.
        """
        payload = {
            "sid": sid,
            "date": date,
            "mi_list": list(micro_ids),
            "quota": quotas or ALL_QUOTAS,
            "pb_ver": 1,
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
        try:
            content = self._post_request(self.uris['micro_count_by_day'], payload=payload,
                                         headers=dict(headers), response_type='raw')
            day = parse_count_by_day(content, list(micro_ids))
        except Exception as err:  # noqa: BLE001 - token expiry shows up as HTTP error or junk body
            _LOGGER.info("micro_count_by_day failed (%s), logging in again and retrying", err)
            self.login()
            content = self._post_request(self.uris['micro_count_by_day'], payload=payload,
                                         headers=dict(headers), response_type='raw')
            day = parse_count_by_day(content, list(micro_ids))
        return day

    def module_count_by_day(self, sid, date, micro_id, ports, quotas=None):
        """Per-panel (per-port) day series: DC power, voltage, current.

        This endpoint answers with an EMPTY body when several quotas are asked
        at once (unlike the microinverter one), so each quota is its own request
        and the results are merged.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
        merged = None
        for quota in quotas or MODULE_QUOTAS:
            payload = {
                "sid": sid,
                "date": date,
                "mi_list": [{"id": micro_id, "port": p} for p in ports],
                "quota": [quota],
                "pb_ver": 1,
            }
            try:
                content = self._post_request(self.uris['module_count_by_day'], payload=payload,
                                             headers=dict(headers), response_type='raw')
                part = parse_module_count_by_day(content, date)
            except Exception as err:  # noqa: BLE001 - expired token shows up as an error
                _LOGGER.info("module_count_by_day failed (%s), logging in again and retrying", err)
                self.login()
                content = self._post_request(self.uris['module_count_by_day'], payload=payload,
                                             headers=dict(headers), response_type='raw')
                part = parse_module_count_by_day(content, date)
            if not part.series:
                _LOGGER.warning("Empty %s panel data for microinverter %s (%d bytes)",
                                quota, micro_id, len(content or b""))
            if merged is None:
                merged = part
            else:
                if len(part.times) > len(merged.times):
                    merged.times = part.times
                for key, values in part.series.items():
                    merged.series.setdefault(key, {}).update(values)
                merged.quota_times.update(part.quota_times)
        return merged

    @cached(cache=TTLCache(maxsize=100, ttl=300), lock=_CACHE_LOCK)
    def select_device_of_tree(self, station_id):
        """Get device tree for a station including DTU and microinverters."""
        payload = {
            "id": station_id,
        }
        response = self._post_request(self.uris['select_device_of_tree'], payload=payload)
        return response.get('data', [])

    def parse_dtu_info(self, tree_data):
        """Parse DTU information from select_device_of_tree response.
        
        Returns a list of DTU info dictionaries with:
        - id: DTU ID
        - sn: Serial number
        - model_no: Model name
        - connect: Connection status
        - soft_ver: Software version
        - hard_ver: Hardware version
        - microinverters: List of child microinverter IDs
        """
        dtu_list = []
        
        for device in tree_data:
            if device.get('type') == 1:  # Type 1 appears to be DTU
                dtu_info = {
                    'id': device.get('id'),
                    'sn': device.get('sn'),
                    'model_no': device.get('model_no'),
                    'connect': device.get('warn_data', {}).get('connect', False),
                    'soft_ver': device.get('soft_ver'),
                    'hard_ver': device.get('hard_ver'),
                    'microinverters': [child.get('id') for child in device.get('children', [])]
                }
                dtu_list.append(dtu_info)
        
        return dtu_list

    # ============================================================================
    # CONTROL OPERATIONS
    # ============================================================================

    
    def turn_off_microinverter(self, dev_sn, dev_type, dtu_sn):
        """Turn off the microinverter. [UNUSED]"""
        self.put_command(7, dev_sn, 3, dtu_sn)

    def set_power_limit(self, sid, power_limit):
        """Set the power limit for a microinverter."""
        power_limit = min(100, int(power_limit))
        power_limit = max(5, int(power_limit))
        payload = {
            "action": 8,
            "data": {
                "sid": sid,
                "power_limit": power_limit,
                "enable": 1
            }
        }

        _LOGGER.debug(f"Setting power limit for SID {sid} to {power_limit}%")
        
        uri = 'pvm-ctl/api/0/dev/command/put'
        return self._put_request(uri, payload=payload)

    # ============================================================================
    # SYSTEM MAPPING AND DATA PROCESSING
    # ============================================================================
    
