#!/usr/bin/env python3
"""
eero Business Dashboard - Multi-Network Monitoring
Adapted from minirack-pi dashboard architecture.
Serves a card-based dashboard for monitoring multiple eero networks.
"""
import os
import sys
import json
import requests
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, render_template, Response
from flask_cors import CORS
import logging
import pytz

from app.geocoding import GeocodingService
from app.alerts import process_network_alerts, get_recent_alerts, get_unacknowledged_count, ack_alert
from app.reports import generate_report_data, generate_csv, generate_pdf

# Configuration
VERSION = "2.0.7"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("EERO_CONFIG_FILE", os.path.join(BASE_DIR, "config.json"))
DATA_CACHE_FILE = os.environ.get("EERO_CACHE_FILE", os.path.join(BASE_DIR, "data_cache.json"))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Setup logging
log_dir = os.environ.get("EERO_LOG_DIR", os.path.join(BASE_DIR, "logs"))
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'dashboard.log')),
        logging.StreamHandler()
    ]
)

# Flask app
app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR
)
CORS(app)


# ---------------------------------------------------------------------------
# Request Logging & Error Handlers
# ---------------------------------------------------------------------------

@app.before_request
def log_request():
    """Log incoming API requests."""
    if request.path.startswith('/api/'):
        logging.debug("API request: %s %s", request.method, request.path)


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('index.html'), 404


@app.errorhandler(500)
def server_error(e):
    logging.error("Internal server error: %s", str(e))
    return jsonify({'error': 'Internal server error'}), 500


# ---------------------------------------------------------------------------
# Configuration Management
# ---------------------------------------------------------------------------

def load_config():
    """Load configuration from JSON file."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Migrate old single-network config to multi-network format
                if 'network_id' in config and 'networks' not in config:
                    config['networks'] = [{
                        'id': config.get('network_id', ''),
                        'name': 'Primary Network',
                        'email': '',
                        'token': '',
                        'active': True
                    }]
                return config
    except Exception as e:
        logging.error("Config load error: %s", str(e))

    return {
        "networks": [],
        "environment": os.environ.get("EERO_ENV", "development"),
        "api_url": "api-user.e2ro.com",
        "timezone": os.environ.get("EERO_TIMEZONE", "UTC")
    }


def save_config(config):
    """Save configuration to JSON file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logging.error("Config save error: %s", str(e))
        return False


def get_timezone_aware_now():
    """Get current time in configured timezone."""
    try:
        config = load_config()
        tz_name = config.get('timezone', 'UTC')
        tz = pytz.timezone(tz_name)
        return datetime.now(tz)
    except Exception as e:
        logging.warning("Timezone error, using UTC: %s", str(e))
        return datetime.now(pytz.UTC)


# ---------------------------------------------------------------------------
# Data Cache Persistence
# ---------------------------------------------------------------------------

# Traffic history — persisted to disk alongside data cache.
# Initialised here so load_data_cache() can restore it on startup.
_traffic_history = {}  # { network_id: [ { timestamp, ratio, condition, ... } ] }

def save_data_cache():
    """Save data cache to disk for persistence."""
    try:
        cache_copy = {}
        for key, value in data_cache.items():
            if key == 'networks':
                cache_copy[key] = {}
                for net_id, net_data in value.items():
                    cache_copy[key][net_id] = net_data.copy()
            else:
                cache_copy[key] = value.copy() if isinstance(value, dict) else value

        cache_copy['_saved_at'] = get_timezone_aware_now().isoformat()

        # Persist traffic history alongside the data cache
        cache_copy['_traffic_history'] = _traffic_history

        with open(DATA_CACHE_FILE, 'w') as f:
            json.dump(cache_copy, f, indent=2)

        logging.info("Data cache saved to disk")
        return True
    except Exception as e:
        logging.error("Failed to save data cache: %s", str(e))
        return False


def load_data_cache():
    """Load data cache from disk."""
    try:
        if os.path.exists(DATA_CACHE_FILE):
            with open(DATA_CACHE_FILE, 'r') as f:
                saved_cache = json.load(f)

            saved_at = saved_cache.get('_saved_at')
            if saved_at:
                saved_time = datetime.fromisoformat(saved_at.replace('Z', '+00:00'))
                current_time = get_timezone_aware_now()

                if saved_time.tzinfo is None:
                    saved_time = pytz.UTC.localize(saved_time)
                if current_time.tzinfo != saved_time.tzinfo:
                    current_time = current_time.astimezone(pytz.UTC)
                    saved_time = saved_time.astimezone(pytz.UTC)

                age_hours = (current_time - saved_time).total_seconds() / 3600
                if age_hours > 24:
                    logging.info("Cached data is %.1f hours old, starting fresh", age_hours)
                    return None

            if '_saved_at' in saved_cache:
                del saved_cache['_saved_at']

            # Restore traffic history if present
            global _traffic_history
            if '_traffic_history' in saved_cache:
                _traffic_history = saved_cache.pop('_traffic_history')
                logging.info("Restored traffic history from disk (%d networks)", len(_traffic_history))

            logging.info("Loaded data cache from disk")
            return saved_cache
    except Exception as e:
        logging.error("Failed to load data cache: %s", str(e))

    return None


# ---------------------------------------------------------------------------
# eero API Integration
# ---------------------------------------------------------------------------

class EeroAPI:
    """Interface to the eero Business API supporting multiple networks."""

    def __init__(self):
        self.session = requests.Session()
        self.config = load_config()
        self.api_url = self.config.get('api_url', 'api-user.e2ro.com')
        self.api_base = "https://" + self.api_url + "/2.2"
        self.network_tokens = {}
        self.load_all_tokens()

    def load_all_tokens(self):
        """Load API tokens for all configured networks."""
        try:
            networks = self.config.get('networks', [])
            for network in networks:
                network_id = network.get('id')
                if not network_id:
                    continue
                token_file = os.path.join(BASE_DIR, f".eero_token_{network_id}")
                if os.path.exists(token_file):
                    with open(token_file, 'r') as f:
                        self.network_tokens[network_id] = f.read().strip()
                else:
                    token = network.get('token', '')
                    if token:
                        self.network_tokens[network_id] = token
        except Exception as e:
            logging.error("Token loading error: %s", str(e))

    def get_headers(self, network_id):
        """Build request headers with auth token for a specific network."""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': f'eero-Business-Dashboard/{VERSION}'
        }
        token = self.network_tokens.get(network_id)
        if token:
            headers['X-User-Token'] = token
        return headers

    def get_network_info(self, network_id):
        """Fetch network metadata."""
        try:
            url = f"{self.api_base}/networks/{network_id}"
            response = self.session.get(url, headers=self.get_headers(network_id), timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'data' in data:
                return data['data']
            return {}
        except Exception as e:
            logging.error("Network info fetch error for %s: %s", network_id, str(e))
            return {}

    def get_all_devices(self, network_id):
        """Fetch all devices for a specific network with retry logic."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                url = f"{self.api_base}/networks/{network_id}/devices"
                response = self.session.get(
                    url, headers=self.get_headers(network_id), timeout=15
                )
                response.raise_for_status()
                data = response.json()

                if 'data' in data:
                    devices = (
                        data['data']
                        if isinstance(data['data'], list)
                        else data['data'].get('devices', [])
                    )
                    logging.info(
                        "Retrieved %d devices from network %s (attempt %d)",
                        len(devices), network_id, attempt + 1
                    )
                    return devices

                return []

            except requests.exceptions.Timeout:
                logging.warning(
                    "API timeout for network %s on attempt %d", network_id, attempt + 1
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                logging.warning(
                    "API request error for network %s on attempt %d: %s",
                    network_id, attempt + 1, str(e)
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logging.error(
                    "Device fetch error for network %s on attempt %d: %s",
                    network_id, attempt + 1, str(e)
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        logging.error("All device fetch attempts failed for network %s", network_id)
        return []

    def get_network_activity(self, network_id):
        """Fetch the activity/event log from the eero network."""
        try:
            url = f"{self.api_base}/networks/{network_id}/activity"
            response = self.session.get(url, headers=self.get_headers(network_id), timeout=15)
            if response.status_code == 200:
                data = response.json()
                return data.get('data', data) if isinstance(data, dict) else data
            # Try alternate endpoint
            url2 = f"{self.api_base}/networks/{network_id}/updates"
            response2 = self.session.get(url2, headers=self.get_headers(network_id), timeout=15)
            if response2.status_code == 200:
                data2 = response2.json()
                return data2.get('data', data2) if isinstance(data2, dict) else data2
            logging.warning("Activity log not available for network %s (status %d / %d)",
                            network_id, response.status_code, response2.status_code)
            return None
        except Exception as e:
            logging.warning("Activity fetch error for %s: %s", network_id, e)
            return None



# ---------------------------------------------------------------------------
# Device Processing Utilities
# ---------------------------------------------------------------------------

def detect_device_os(device):
    """Detect device OS from manufacturer and hostname."""
    manufacturer = str(device.get('manufacturer', '')).lower()
    hostname = str(device.get('hostname', '')).lower()
    text = manufacturer + " " + hostname

    if any(k in manufacturer for k in ['amazon', 'amazon technologies']):
        return 'Amazon'
    elif any(k in text for k in ['echo', 'alexa', 'fire tv', 'kindle']):
        return 'Amazon'
    elif any(k in manufacturer for k in ['apple', 'apple inc']):
        return 'iOS'
    elif any(k in text for k in ['iphone', 'ipad', 'mac', 'ios', 'apple']):
        return 'iOS'
    elif any(k in manufacturer for k in ['samsung', 'google', 'lg electronics', 'htc', 'sony', 'motorola', 'huawei', 'xiaomi', 'oneplus']):
        return 'Android'
    elif any(k in text for k in ['android', 'pixel', 'galaxy']):
        return 'Android'
    elif any(k in manufacturer for k in ['microsoft', 'dell', 'hp', 'lenovo', 'asus', 'acer', 'msi']):
        return 'Windows'
    elif any(k in text for k in ['windows', 'microsoft', 'surface']):
        return 'Windows'
    elif any(k in manufacturer for k in ['sony computer entertainment', 'nintendo']):
        return 'Gaming'
    elif any(k in text for k in ['playstation', 'xbox', 'nintendo', 'steam deck']):
        return 'Gaming'
    elif any(k in manufacturer for k in ['roku', 'nvidia', 'chromecast']):
        return 'Streaming'
    elif any(k in text for k in ['roku', 'chromecast', 'nvidia shield', 'apple tv']):
        return 'Streaming'
    else:
        return 'Other'


def parse_frequency(interface_info):
    """Parse frequency information from device interface data."""
    try:
        if interface_info is None:
            return 'N/A', 'Unknown'
        freq = interface_info.get('frequency')
        if freq is None or freq == 'N/A' or freq == '':
            return 'N/A', 'Unknown'
        freq_value = float(freq)
        if 2.4 <= freq_value < 2.5:
            band = '2.4GHz'
        elif 5.0 <= freq_value < 6.0:
            band = '5GHz'
        elif 6.0 <= freq_value < 7.0:
            band = '6GHz'
        else:
            band = 'Unknown'
        return str(freq) + " GHz", band
    except (ValueError, TypeError):
        return 'N/A', 'Unknown'


def convert_signal_dbm_to_percent(signal_dbm):
    """Convert dBm to percentage (0-100)."""
    try:
        if not signal_dbm or signal_dbm == 'N/A':
            return 0
        dbm = float(str(signal_dbm).replace(' dBm', '').strip())
        if dbm >= -50:
            return 100
        elif dbm <= -100:
            return 0
        else:
            return int(2 * (dbm + 100))
    except (ValueError, TypeError):
        return 0


def get_signal_quality(signal_dbm):
    """Get signal quality description from dBm value."""
    try:
        if not signal_dbm or signal_dbm == 'N/A':
            return 'Unknown'
        dbm = float(str(signal_dbm).replace(' dBm', '').strip())
        if dbm >= -50:
            return 'Excellent'
        elif dbm >= -60:
            return 'Very Good'
        elif dbm >= -70:
            return 'Good'
        elif dbm >= -80:
            return 'Fair'
        else:
            return 'Poor'
    except (ValueError, TypeError):
        return 'Unknown'


def calculate_health_status(total_devices, online_devices):
    """
    Calculate network health status based on device online ratio.
    Returns: 'healthy', 'degraded', or 'offline'
    """
    if total_devices == 0 or online_devices == 0:
        return 'offline'
    online_pct = online_devices / total_devices
    if online_pct > 0.5:
        return 'healthy'
    else:
        return 'degraded'


def calculate_bandwidth_utilization(usage_mbps, capacity_mbps):
    """
    Calculate bandwidth utilization as a percentage.
    Returns a value clamped between 0 and 100.
    """
    if capacity_mbps <= 0:
        return 0.0
    utilization = (usage_mbps / capacity_mbps) * 100
    return max(0.0, min(100.0, utilization))


# Max age in seconds for a device to be considered "recently active"
RECENTLY_ACTIVE_THRESHOLD = 900  # 15 minutes


def is_device_active(device):
    """Determine if a device is effectively connected.

    The eero API sometimes reports wireless clients (especially on guest
    networks) as connected=False even when they are actively using the
    network.  We treat a device as active if:
      1. The API says connected=True, OR
      2. The device has a last_active timestamp within the last 15 minutes.
    """
    if device.get('connected'):
        return True

    last_active = device.get('last_active', '')
    if not last_active:
        return False

    try:
        la_time = datetime.fromisoformat(last_active.replace('Z', '+00:00'))
        now = datetime.now(pytz.UTC)
        if la_time.tzinfo is None:
            la_time = pytz.UTC.localize(la_time)
        age_seconds = (now - la_time).total_seconds()
        return age_seconds <= RECENTLY_ACTIVE_THRESHOLD
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Data Cache Initialization & Update
# ---------------------------------------------------------------------------

# Initialize API
eero_api = EeroAPI()


def initialize_data_cache():
    """Initialize data cache, loading from disk if available."""
    default_cache = {
        'networks': {},
        'combined': {
            'connected_users': [],
            'device_os': {},
            'frequency_distribution': {},
            'signal_strength_avg': [],
            'devices': [],
            'total_devices': 0,
            'wireless_devices': 0,
            'wired_devices': 0,
            'last_update': None,
            'active_networks': 0
        },
        'map_data': {
            'locations': []
        }
    }

    saved_cache = load_data_cache()
    if saved_cache:
        for key in default_cache:
            if key in saved_cache:
                default_cache[key] = saved_cache[key]
        logging.info("Restored data cache from disk")

    return default_cache


data_cache = initialize_data_cache()


def update_cache():
    """Update data cache with latest device information from all networks."""
    global data_cache
    if not _cache_lock.acquire(blocking=False):
        logging.info("Cache update already in progress, skipping duplicate request")
        return
    try:
        logging.info("Starting multi-network cache update...")
        config = load_config()
        networks = config.get('networks', [])
        active_networks = [n for n in networks if n.get('active', True)]

        if not active_networks:
            logging.warning("No active networks configured")
            return

        combined_devices = []
        combined_os_counts = {
            'iOS': 0, 'Android': 0, 'Windows': 0,
            'Amazon': 0, 'Gaming': 0, 'Streaming': 0, 'Other': 0
        }
        combined_freq_counts = {'2.4GHz': 0, '5GHz': 0, '6GHz': 0}
        combined_signal_values = []
        current_time = get_timezone_aware_now()

        for network in active_networks:
            network_id = network.get('id')
            if not network_id:
                continue

            network_devices = eero_api.get_all_devices(network_id)
            if not network_devices:
                continue

            connected_devices = [d for d in network_devices if is_device_active(d)]
            wireless_devices = [d for d in connected_devices if d.get('wireless')]

            if network_id not in data_cache['networks']:
                data_cache['networks'][network_id] = {
                    'connected_users': [],
                    'signal_strength_avg': [],
                    'devices': [],
                    'last_update': None,
                    'last_successful_update': None
                }

            network_cache = data_cache['networks'][network_id]
            network_device_list = []
            network_os_counts = {
                'iOS': 0, 'Android': 0, 'Windows': 0,
                'Amazon': 0, 'Gaming': 0, 'Streaming': 0, 'Other': 0
            }
            network_freq_counts = {'2.4GHz': 0, '5GHz': 0, '6GHz': 0}
            network_signal_values = []

            for device in connected_devices:
                device_os = detect_device_os(device)
                network_os_counts[device_os] += 1
                combined_os_counts[device_os] += 1

                is_wireless = device.get('wireless', False)
                interface_info = device.get('interface', {}) if is_wireless else {}

                if is_wireless:
                    freq_display, freq_band = parse_frequency(interface_info)
                    if freq_band in network_freq_counts:
                        network_freq_counts[freq_band] += 1
                        combined_freq_counts[freq_band] += 1

                    signal_dbm = interface_info.get('signal_dbm', 'N/A')
                    signal_percent = convert_signal_dbm_to_percent(signal_dbm)
                    signal_quality = get_signal_quality(signal_dbm)

                    if signal_dbm != 'N/A' and signal_dbm is not None:
                        try:
                            if isinstance(signal_dbm, (int, float)):
                                signal_val = float(signal_dbm)
                            else:
                                signal_val = float(
                                    str(signal_dbm).replace(' dBm', '').replace('dBm', '').strip()
                                )
                            if -100 <= signal_val <= -10:
                                network_signal_values.append(signal_val)
                                combined_signal_values.append(signal_val)
                        except (ValueError, TypeError):
                            pass
                else:
                    freq_display = 'Wired'
                    freq_band = 'Wired'
                    signal_dbm = 'N/A'
                    signal_percent = 100
                    signal_quality = 'Wired'

                device_info = {
                    'name': device.get('nickname') or device.get('hostname') or 'Unknown Device',
                    'ip': ', '.join(device.get('ips', [])) if device.get('ips') else 'N/A',
                    'mac': device.get('mac', 'N/A'),
                    'manufacturer': device.get('manufacturer', 'Unknown'),
                    'device_os': device_os,
                    'connection_type': 'Wireless' if is_wireless else 'Wired',
                    'frequency': freq_display,
                    'frequency_band': freq_band,
                    'signal_avg_dbm': f"{signal_dbm} dBm" if signal_dbm != 'N/A' else 'N/A',
                    'signal_avg': signal_percent,
                    'signal_quality': signal_quality,
                    'network_id': network_id,
                    'network_name': network.get('name', f'Network {network_id}')
                }
                network_device_list.append(device_info)
                combined_devices.append(device_info)

            # Update network-specific time-series
            network_connected_users = network_cache.get('connected_users', [])
            network_connected_users.append({
                'timestamp': current_time.isoformat(),
                'count': len(connected_devices),
                'wireless_count': len(wireless_devices)
            })
            if len(network_connected_users) > 168:
                network_connected_users = network_connected_users[-168:]

            network_signal_strength_avg = network_cache.get('signal_strength_avg', [])
            if network_signal_values:
                avg_signal = sum(network_signal_values) / len(network_signal_values)
                network_signal_strength_avg.append({
                    'timestamp': current_time.isoformat(),
                    'avg_dbm': round(avg_signal, 1)
                })
            if len(network_signal_strength_avg) > 168:
                network_signal_strength_avg = network_signal_strength_avg[-168:]

            # Calculate health status based on eero node status (not client devices)
            # Fetch eero nodes to determine infrastructure health
            try:
                eero_url = f"{eero_api.api_base}/networks/{network_id}/eeros"
                eero_resp = eero_api.session.get(
                    eero_url, headers=eero_api.get_headers(network_id), timeout=10
                )
                if eero_resp.status_code == 200:
                    eero_data = eero_resp.json().get('data', [])
                    if isinstance(eero_data, list) and eero_data:
                        total_nodes = len(eero_data)
                        green_nodes = sum(
                            1 for e in eero_data
                            if str(e.get('status', '')).lower() == 'green'
                        )
                        network_cache['eero_count'] = total_nodes
                        network_cache['eero_online'] = green_nodes
                        if green_nodes == total_nodes:
                            health_status = 'healthy'
                        elif green_nodes > 0:
                            health_status = 'degraded'
                        else:
                            health_status = 'offline'
                    else:
                        # No eero nodes data — fall back to client-based heuristic
                        health_status = 'healthy' if len(connected_devices) > 0 else 'offline'
                else:
                    health_status = 'healthy' if len(connected_devices) > 0 else 'offline'
            except Exception as e:
                logging.warning("Eero nodes health check failed for %s: %s", network_id, e)
                health_status = 'healthy' if len(connected_devices) > 0 else 'offline'

            network_cache.update({
                'connected_users': network_connected_users,
                'signal_strength_avg': network_signal_strength_avg,
                'devices': network_device_list,
                'device_os': network_os_counts,
                'frequency_distribution': network_freq_counts,
                'total_devices': len(connected_devices),
                'wireless_devices': len(wireless_devices),
                'wired_devices': len(connected_devices) - len(wireless_devices),
                'health_status': health_status,
                'last_update': current_time.isoformat(),
                'last_successful_update': current_time.isoformat()
            })

            # Bandwidth tracking — eero API may provide speed_mbps on network info
            try:
                net_info = eero_api.get_network_info(network_id)
                speed = net_info.get('speed', {}) if net_info else {}
                upload_mbps = speed.get('up', {}).get('value', 0) or 0
                download_mbps = speed.get('down', {}).get('value', 0) or 0
                capacity_mbps = round(upload_mbps + download_mbps)
                # Estimate usage from device count (rough proxy when API doesn't expose real-time usage)
                usage_mbps = len(connected_devices) * 2.5  # ~2.5 Mbps avg per device
                bw_util = calculate_bandwidth_utilization(usage_mbps, capacity_mbps) if capacity_mbps > 0 else 0.0
            except Exception:
                capacity_mbps = 0
                usage_mbps = 0
                bw_util = 0.0

            network_cache['bandwidth_utilization'] = round(bw_util, 1)
            network_cache['bandwidth_capacity_mbps'] = capacity_mbps
            network_cache['bandwidth_usage_mbps'] = round(usage_mbps, 1)

            # Uptime tracking
            prev_health = network_cache.get('_prev_health')

            if prev_health == 'offline' and health_status != 'offline':
                # Recovery — clear offline timestamp and close open incidents
                network_cache.pop('offline_since', None)
                try:
                    from app.database import get_db_session, UptimeIncident
                    with get_db_session() as session:
                        open_incidents = (
                            session.query(UptimeIncident)
                            .filter(UptimeIncident.network_id == network_id)
                            .filter(UptimeIncident.end_time.is_(None))
                            .all()
                        )
                        for inc in open_incidents:
                            inc.end_time = current_time.isoformat()
                            try:
                                inc_start = datetime.fromisoformat(inc.start_time)
                                if inc_start.tzinfo is None:
                                    inc_start = pytz.UTC.localize(inc_start)
                                duration = int((current_time - inc_start).total_seconds())
                                inc.duration_seconds = max(0, duration)
                            except (ValueError, TypeError):
                                pass
                        session.commit()
                    logging.info("Closed %d open uptime incident(s) for network %s on recovery",
                                 len(open_incidents), network_id)
                except Exception as e:
                    logging.error("Failed to close uptime incidents on recovery for %s: %s",
                                  network_id, e)
            elif health_status == 'offline' and prev_health not in ('offline', None):
                # Genuine new transition to offline
                network_cache['offline_since'] = current_time.isoformat()
                try:
                    from app.database import insert_uptime_incident
                    insert_uptime_incident(
                        network_id=network_id,
                        start_time=current_time.isoformat(),
                    )
                except Exception as e:
                    logging.error("Failed to record uptime incident: %s", e)

            # Always derive offline_since from the first offline alert in the DB
            if health_status == 'offline':
                try:
                    from app.database import get_db_session, Alert
                    with get_db_session() as session:
                        first_alert = (session.query(Alert)
                                       .filter(Alert.network_id == network_id,
                                               Alert.alert_type == 'offline')
                                       .order_by(Alert.created_at.asc())
                                       .first())
                        if first_alert:
                            network_cache['offline_since'] = first_alert.created_at
                except Exception:
                    pass
                # Final fallback
                if not network_cache.get('offline_since'):
                    network_cache['offline_since'] = current_time.isoformat()

            network_cache['_prev_health'] = health_status

            # Calculate uptime percentage (based on connected_users history)
            history = network_cache.get('connected_users', [])
            if history:
                online_points = sum(1 for p in history if p.get('count', 0) > 0)
                uptime_pct = round((online_points / len(history)) * 100, 1)
            else:
                uptime_pct = 100.0
            network_cache['uptime_24h'] = uptime_pct

            # Persist metrics to database
            try:
                from app.database import insert_metric
                avg_sig = round(sum(network_signal_values) / len(network_signal_values), 1) if network_signal_values else None
                insert_metric(
                    network_id=network_id,
                    timestamp=current_time.isoformat(),
                    total_devices=len(connected_devices),
                    wireless_devices=len(wireless_devices),
                    wired_devices=len(connected_devices) - len(wireless_devices),
                    bandwidth_usage_mbps=round(usage_mbps, 1),
                    bandwidth_capacity_mbps=capacity_mbps,
                    bandwidth_utilization=round(bw_util, 1),
                    avg_signal_dbm=avg_sig,
                )
            except Exception as e:
                logging.error("Failed to persist metrics: %s", e)

            # Check for alert-worthy transitions
            network_name = network.get('name', f'Network {network_id}')
            process_network_alerts(network_id, network_name, health_status, bw_util)

        # Update combined cache
        combined_connected_users = data_cache['combined'].get('connected_users', [])
        combined_connected_users.append({
            'timestamp': current_time.isoformat(),
            'count': len(combined_devices)
        })
        if len(combined_connected_users) > 168:
            combined_connected_users = combined_connected_users[-168:]

        combined_signal_strength_avg = data_cache['combined'].get('signal_strength_avg', [])
        if combined_signal_values:
            avg_signal = sum(combined_signal_values) / len(combined_signal_values)
            combined_signal_strength_avg.append({
                'timestamp': current_time.isoformat(),
                'avg_dbm': round(avg_signal, 1)
            })
        if len(combined_signal_strength_avg) > 168:
            combined_signal_strength_avg = combined_signal_strength_avg[-168:]

        combined_wireless = len([d for d in combined_devices if d['connection_type'] == 'Wireless'])
        combined_wired = len(combined_devices) - combined_wireless

        data_cache['combined'].update({
            'connected_users': combined_connected_users,
            'device_os': combined_os_counts,
            'frequency_distribution': combined_freq_counts,
            'signal_strength_avg': combined_signal_strength_avg,
            'devices': combined_devices,
            'total_devices': len(combined_devices),
            'wireless_devices': combined_wireless,
            'wired_devices': combined_wired,
            'last_update': current_time.isoformat(),
            'last_successful_update': current_time.isoformat(),
            'active_networks': len(active_networks)
        })

        logging.info(
            "Multi-network cache updated: %d networks, %d total devices",
            len(active_networks), len(combined_devices)
        )
        save_data_cache()

    except Exception as e:
        logging.error("Multi-network cache update error: %s", str(e))
        current_time = get_timezone_aware_now()
        data_cache['combined']['last_update'] = current_time.isoformat()
        save_data_cache()
    finally:
        _cache_lock.release()


def filter_data_by_timerange(data, hours):
    """Filter time-series data by hours."""
    if not data or hours == 0:
        return data
    cutoff_time = get_timezone_aware_now() - timedelta(hours=hours)
    return [
        entry for entry in data
        if datetime.fromisoformat(entry['timestamp']) >= cutoff_time
    ]


# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Serve main dashboard page."""
    return render_template('index.html')


@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'version': VERSION})


@app.route('/api/dashboard')
def get_dashboard_data():
    """Get combined dashboard data for all networks."""
    return jsonify(data_cache['combined'])


@app.route('/api/dashboard/<int:hours>')
def get_dashboard_data_filtered(hours):
    """Get dashboard data filtered by time range."""
    filtered_cache = data_cache['combined'].copy()
    filtered_cache['connected_users'] = filter_data_by_timerange(
        data_cache['combined']['connected_users'], hours
    )
    filtered_cache['signal_strength_avg'] = filter_data_by_timerange(
        data_cache['combined']['signal_strength_avg'], hours
    )
    return jsonify(filtered_cache)


@app.route('/api/networks')
def get_networks():
    """Get all configured networks with authentication status."""
    config = load_config()
    networks = config.get('networks', [])

    for network in networks:
        network_id = network.get('id')
        network['authenticated'] = network_id in eero_api.network_tokens

    return jsonify({'networks': networks})


@app.route('/api/network-stats')
def get_network_stats():
    """Get detailed statistics for each network."""
    try:
        config = load_config()
        networks = config.get('networks', [])
        active_networks = [n for n in networks if n.get('active', True)]

        network_stats = []
        for network in active_networks:
            network_id = network.get('id')
            if not network_id or network_id not in data_cache.get('networks', {}):
                continue

            network_cache = data_cache['networks'][network_id]
            network_info = {
                'id': network_id,
                'name': network.get('name', f'Network {network_id}'),
                'authenticated': network_id in eero_api.network_tokens,
                'total_devices': network_cache.get('total_devices', 0),
                'wireless_devices': network_cache.get('wireless_devices', 0),
                'wired_devices': network_cache.get('wired_devices', 0),
                'health_status': network_cache.get('health_status', 'offline'),
                'bandwidth_utilization': network_cache.get('bandwidth_utilization', 0.0),
                'bandwidth_usage_mbps': network_cache.get('bandwidth_usage_mbps', 0.0),
                'bandwidth_capacity_mbps': network_cache.get('bandwidth_capacity_mbps', 0),
                'uptime_24h': network_cache.get('uptime_24h', 100.0),
                'device_os': network_cache.get('device_os', {}),
                'frequency_distribution': network_cache.get('frequency_distribution', {}),
                'last_update': network_cache.get('last_update'),
                'last_successful_update': network_cache.get('last_successful_update'),
                'address': network.get('address', {}),
                'offline_since': network_cache.get('offline_since'),
                'eero_count': network_cache.get('eero_count', 0),
                'eero_online': network_cache.get('eero_online', 0),
                'signal_strength_avg': network_cache.get('signal_strength_avg', []),
                'site_type': network.get('site_type', 'store'),
            }
            network_stats.append(network_info)

        return jsonify({
            'networks': network_stats,
            'total_networks': len(network_stats),
            'combined_stats': data_cache.get('combined', {})
        })

    except Exception as e:
        logging.error("Network stats error: %s", str(e))
        return jsonify({'networks': [], 'total_networks': 0, 'combined_stats': {}}), 500


@app.route('/api/network/<network_id>/detail')
def get_network_detail(network_id):
    """Get detailed information for a single network including per-eero-device groupings."""
    try:
        config = load_config()
        networks = config.get('networks', [])
        network_cfg = next((n for n in networks if n.get('id') == network_id), None)

        if not network_cfg:
            return jsonify({'error': 'Network not found'}), 404

        network_cache = data_cache.get('networks', {}).get(network_id, {})
        devices = network_cache.get('devices', [])

        # Try to get eero node info from the API
        eero_nodes = []
        try:
            url = f"{eero_api.api_base}/networks/{network_id}/eeros"
            resp = eero_api.session.get(url, headers=eero_api.get_headers(network_id), timeout=10)
            if resp.status_code == 200:
                resp_data = resp.json()
                nodes_data = resp_data.get('data', [])
                if isinstance(nodes_data, list):
                    eero_nodes = nodes_data
        except Exception as e:
            logging.warning("Could not fetch eero nodes for %s: %s", network_id, e)

        # Build eero node list with connected clients
        eero_devices = []
        for node in eero_nodes:
            node_url = node.get('url', '')
            # eero nodes API uses 'status' field (green/yellow/red), not 'connected'
            api_status = str(node.get('status', '')).lower()
            if api_status == 'green':
                node_status = 'online'
            elif api_status == 'yellow':
                node_status = 'degraded'
            else:
                node_status = 'offline'
            node_info = {
                'name': node.get('location', node.get('serial', 'Unknown eero')),
                'model': node.get('model', 'Unknown'),
                'serial': node.get('serial', 'N/A'),
                'status': node_status,
                'is_gateway': node.get('gateway', False),
                'ip': node.get('ip_address', 'N/A'),
                'mesh_quality': node.get('mesh_quality_bars', None),
                'os_version': node.get('os_version', 'Unknown'),
                'url': node_url,
                'clients': []
            }
            eero_devices.append(node_info)

        # If we got eero nodes, try to match devices to their source eero
        # The eero API device list may include a 'source' field with the eero URL
        if eero_nodes:
            raw_devices = eero_api.get_all_devices(network_id)
            unmatched = []
            for raw_dev in (raw_devices or []):
                if not is_device_active(raw_dev):
                    continue
                source_url = raw_dev.get('source', {}).get('url', '') if isinstance(raw_dev.get('source'), dict) else str(raw_dev.get('source', ''))
                matched = False
                for eero_dev in eero_devices:
                    if source_url and eero_dev['url'] and source_url in eero_dev['url']:
                        eero_dev['clients'].append(_build_client_info(raw_dev))
                        matched = True
                        break
                if not matched:
                    unmatched.append(_build_client_info(raw_dev))

            # If nothing matched (API doesn't provide source), fall back to cached devices
            if all(len(e['clients']) == 0 for e in eero_devices) and devices:
                unmatched = devices
        else:
            unmatched = devices

        # Frequency breakdown
        freq_counts = {'2.4GHz': 0, '5GHz': 0, '6GHz': 0, 'Wired': 0}
        all_clients = devices if not eero_nodes else unmatched
        for eero_dev in eero_devices:
            all_clients = all_clients + eero_dev.get('clients', []) if eero_nodes else all_clients
        for d in (devices or []):
            band = d.get('frequency_band', 'Unknown')
            if band in freq_counts:
                freq_counts[band] += 1

        address = network_cfg.get('address', {})

        # Query alert history for the last 7 days
        alert_history = []
        try:
            from app.database import get_db_session, Alert
            now = get_timezone_aware_now()
            seven_days_ago = (now - timedelta(days=7)).isoformat()
            with get_db_session() as session:
                alerts = (
                    session.query(Alert)
                    .filter(Alert.network_id == network_id)
                    .filter(Alert.created_at >= seven_days_ago)
                    .order_by(Alert.created_at.desc())
                    .all()
                )
                alert_history = [
                    {
                        'id': a.id,
                        'alert_type': a.alert_type,
                        'severity': a.severity,
                        'message': a.message,
                        'created_at': a.created_at,
                        'acknowledged': a.acknowledged,
                    }
                    for a in alerts
                ]
        except Exception as e:
            logging.warning("Could not fetch alert history for %s: %s", network_id, e)

        # Check firmware consistency across eero nodes
        from app.computations import check_firmware_consistency
        version_list = [ed.get('os_version', 'Unknown') for ed in eero_devices]
        firmware_consistent = check_firmware_consistency(version_list)

        detail = {
            'id': network_id,
            'name': network_cfg.get('name', f'Network {network_id}'),
            'address': address,
            'health_status': network_cache.get('health_status', 'offline'),
            'bandwidth_utilization': network_cache.get('bandwidth_utilization', 0),
            'bandwidth_usage_mbps': network_cache.get('bandwidth_usage_mbps', 0),
            'bandwidth_capacity_mbps': network_cache.get('bandwidth_capacity_mbps', 0),
            'uptime_24h': network_cache.get('uptime_24h', 100.0),
            'total_devices': network_cache.get('total_devices', 0),
            'wireless_devices': network_cache.get('wireless_devices', 0),
            'wired_devices': network_cache.get('wired_devices', 0),
            'device_os': network_cache.get('device_os', {}),
            'frequency_distribution': network_cache.get('frequency_distribution', {}),
            'last_update': network_cache.get('last_update'),
            'eero_nodes': eero_devices,
            'unmatched_devices': unmatched if eero_nodes else [],
            'all_devices': devices,
            'connected_users': network_cache.get('connected_users', []),
            'signal_strength_avg': network_cache.get('signal_strength_avg', []),
            'alert_history': alert_history,
            'firmware_consistent': firmware_consistent,
        }

        return jsonify(detail)

    except Exception as e:
        logging.error("Network detail error for %s: %s", network_id, str(e))
        return jsonify({'error': str(e)}), 500


def _build_client_info(raw_device):
    """Build a client info dict from a raw eero API device object."""
    is_wireless = raw_device.get('wireless', False)
    interface_info = raw_device.get('interface', {}) if is_wireless else {}

    if is_wireless:
        freq_display, freq_band = parse_frequency(interface_info)
        signal_dbm = interface_info.get('signal_dbm', 'N/A')
        signal_percent = convert_signal_dbm_to_percent(signal_dbm)
        signal_quality = get_signal_quality(signal_dbm)
    else:
        freq_display = 'Wired'
        freq_band = 'Wired'
        signal_dbm = 'N/A'
        signal_percent = 100
        signal_quality = 'Wired'

    return {
        'name': raw_device.get('nickname') or raw_device.get('hostname') or 'Unknown Device',
        'ip': ', '.join(raw_device.get('ips', [])) if raw_device.get('ips') else 'N/A',
        'mac': raw_device.get('mac', 'N/A'),
        'manufacturer': raw_device.get('manufacturer', 'Unknown'),
        'device_os': detect_device_os(raw_device),
        'connection_type': 'Wireless' if is_wireless else 'Wired',
        'frequency': freq_display,
        'frequency_band': freq_band,
        'signal_avg_dbm': f"{signal_dbm} dBm" if signal_dbm != 'N/A' else 'N/A',
        'signal_avg': signal_percent,
        'signal_quality': signal_quality,
    }


@app.route('/api/devices')
def get_devices():
    """Get all devices across all networks."""
    return jsonify({
        'devices': data_cache['combined'].get('devices', []),
        'count': len(data_cache['combined'].get('devices', []))
    })


@app.route('/api/version')
def get_version():
    """Get dashboard version and configuration info."""
    config = load_config()
    current_time = get_timezone_aware_now()
    networks = config.get('networks', [])

    return jsonify({
        'version': VERSION,
        'networks_count': len(networks),
        'environment': config.get('environment', 'development'),
        'api_url': config.get('api_url', 'api-user.e2ro.com'),
        'timezone': config.get('timezone', 'UTC'),
        'authenticated': len(eero_api.network_tokens) > 0,
        'timestamp': current_time.isoformat(),
        'local_time': current_time.strftime('%Y-%m-%d %H:%M:%S %Z')
    })


@app.route('/api/map-data')
def get_map_data():
    """Return location data for all networks with addresses for map rendering."""
    config = load_config()
    networks = config.get('networks', [])
    locations = []

    for network in networks:
        address = network.get('address', {})
        if not address or not address.get('lat') or not address.get('lng'):
            continue

        network_id = network.get('id')
        network_cache = data_cache.get('networks', {}).get(network_id, {})

        locations.append({
            'network_id': network_id,
            'name': network.get('name', f'Network {network_id}'),
            'address': address.get('formatted', ''),
            'lat': address['lat'],
            'lng': address['lng'],
            'health_status': network_cache.get('health_status', 'offline'),
            'total_devices': network_cache.get('total_devices', 0),
        })

    return jsonify({'locations': locations})


# ---------------------------------------------------------------------------
# Network Management API Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/admin/networks', methods=['POST'])
def add_network():
    """Add a new network to monitor."""
    try:
        data = request.get_json()
        network_id = data.get('network_id', '').strip()
        email = data.get('email', '').strip()
        name = data.get('name', '').strip() or f'Network {network_id}'

        if not network_id or not network_id.isdigit():
            return jsonify({'success': False, 'message': 'Invalid network ID'}), 400

        if not email or '@' not in email:
            return jsonify({'success': False, 'message': 'Invalid email address'}), 400

        config = load_config()
        networks = config.get('networks', [])

        if any(n.get('id') == network_id for n in networks):
            return jsonify({'success': False, 'message': 'Network already exists'}), 400

        if len(networks) >= 6:
            return jsonify({'success': False, 'message': 'Maximum 6 networks allowed'}), 400

        new_network = {
            'id': network_id,
            'name': name,
            'email': email,
            'token': '',
            'active': True,
            'site_type': data.get('site_type', 'store')
        }
        networks.append(new_network)
        config['networks'] = networks

        if save_config(config):
            return jsonify({
                'success': True,
                'message': f'Network {name} added. Please authenticate to start monitoring.',
                'network': new_network
            })

        return jsonify({'success': False, 'message': 'Failed to save configuration'}), 500

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/networks/<network_id>', methods=['DELETE'])
def remove_network(network_id):
    """Remove a network from monitoring."""
    try:
        config = load_config()
        networks = config.get('networks', [])
        original_count = len(networks)

        networks = [n for n in networks if n.get('id') != network_id]
        if len(networks) == original_count:
            return jsonify({'success': False, 'message': 'Network not found'}), 404

        config['networks'] = networks
        if save_config(config):
            token_file = os.path.join(BASE_DIR, f".eero_token_{network_id}")
            if os.path.exists(token_file):
                os.remove(token_file)
            if network_id in eero_api.network_tokens:
                del eero_api.network_tokens[network_id]
            return jsonify({'success': True, 'message': f'Network {network_id} removed'})

        return jsonify({'success': False, 'message': 'Failed to save configuration'}), 500

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/networks/<network_id>/auth', methods=['POST'])
def authenticate_network(network_id):
    """Two-step authentication for a specific network."""
    try:
        data = request.get_json()
        step = data.get('step', 'send')

        config = load_config()
        networks = config.get('networks', [])
        network = next((n for n in networks if n.get('id') == network_id), None)

        if not network:
            return jsonify({'success': False, 'message': 'Network not found'}), 404

        if step == 'send':
            email = data.get('email', '').strip() or network.get('email', '')
            if not email or '@' not in email:
                return jsonify({'success': False, 'message': 'Valid email address required'}), 400

            response = requests.post(
                f"https://{eero_api.api_url}/2.2/pro/login",
                json={"login": email},
                timeout=10
            )
            response.raise_for_status()
            response_data = response.json()

            if 'data' not in response_data or 'user_token' not in response_data['data']:
                return jsonify({'success': False, 'message': 'Failed to generate token'}), 500

            temp_token_file = os.path.join(BASE_DIR, f".eero_token_{network_id}.temp")
            with open(temp_token_file, 'w') as f:
                f.write(response_data['data']['user_token'])

            return jsonify({'success': True, 'message': f'Verification code sent to {email}'})

        elif step == 'verify':
            code = data.get('code', '').strip()
            if not code:
                return jsonify({'success': False, 'message': 'Code required'}), 400

            temp_token_file = os.path.join(BASE_DIR, f".eero_token_{network_id}.temp")
            if not os.path.exists(temp_token_file):
                return jsonify({'success': False, 'message': 'Please restart authentication process'}), 400

            with open(temp_token_file, 'r') as f:
                token = f.read().strip()

            verify_response = requests.post(
                f"https://{eero_api.api_url}/2.2/login/verify",
                headers={"X-User-Token": token, "Content-Type": "application/x-www-form-urlencoded"},
                data={"code": code},
                timeout=10
            )
            verify_response.raise_for_status()
            verify_data = verify_response.json()

            if (verify_data.get('data', {}).get('email', {}).get('verified') or
                    verify_data.get('data', {}).get('verified') or
                    verify_response.status_code == 200):

                token_file = os.path.join(BASE_DIR, f".eero_token_{network_id}")
                with open(token_file, 'w') as f:
                    f.write(token)

                if os.path.exists(temp_token_file):
                    os.remove(temp_token_file)

                eero_api.network_tokens[network_id] = token
                return jsonify({'success': True, 'message': f'Network {network_id} authenticated successfully!'})
            else:
                return jsonify({'success': False, 'message': 'Verification failed. Please check the code.'}), 400

    except requests.RequestException as e:
        logging.error("Network authentication error for %s: %s", network_id, str(e))
        return jsonify({'success': False, 'message': f'Network error: {str(e)}'}), 500
    except Exception as e:
        logging.error("Network authentication error for %s: %s", network_id, str(e))
        return jsonify({'success': False, 'message': f'Authentication error: {str(e)}'}), 500


@app.route('/api/admin/networks/<network_id>/address', methods=['PUT'])
def update_network_address(network_id):
    """Set or update the physical address for a network location."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Request body required'}), 400

        config = load_config()
        networks = config.get('networks', [])
        network = next((n for n in networks if n.get('id') == network_id), None)

        if not network:
            return jsonify({'success': False, 'message': 'Network not found'}), 404

        address_dict = {
            'street': data.get('street', '').strip(),
            'city': data.get('city', '').strip(),
            'state': data.get('state', '').strip(),
            'zip': data.get('zip', '').strip(),
            'country': data.get('country', '').strip() or 'US',
        }

        geo = GeocodingService()
        if not geo.validate_address(address_dict):
            return jsonify({
                'success': False,
                'message': 'Address must include street, city, and state'
            }), 400

        # Build a formatted address string
        formatted_parts = [address_dict['street'], address_dict['city'], address_dict['state']]
        if address_dict['zip']:
            formatted_parts.append(address_dict['zip'])
        if address_dict['country']:
            formatted_parts.append(address_dict['country'])
        formatted = ', '.join(formatted_parts)

        # Attempt geocoding
        geo_result = geo.geocode(address_dict)
        if geo_result:
            address_dict['lat'] = geo_result['lat']
            address_dict['lng'] = geo_result['lng']
            address_dict['formatted'] = geo_result['formatted']
        else:
            # Store address without coordinates when geocoding fails / no API key
            address_dict['lat'] = None
            address_dict['lng'] = None
            address_dict['formatted'] = formatted

        network['address'] = address_dict
        config['networks'] = networks
        save_config(config)

        return jsonify({'success': True, 'address': address_dict})

    except Exception as e:
        logging.error("Address update error for %s: %s", network_id, str(e))
        return jsonify({'success': False, 'message': str(e)}), 500






# ---------------------------------------------------------------------------
# Logo Upload / Serve
# ---------------------------------------------------------------------------

LOGO_DIR = os.path.join(STATIC_DIR, 'uploads')
os.makedirs(LOGO_DIR, exist_ok=True)
ALLOWED_LOGO_EXTENSIONS = {'png', 'svg', 'webp', 'gif'}
MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2 MB


@app.route('/api/admin/networks/<network_id>/site-type', methods=['PUT'])
def update_site_type(network_id):
    """Update the site type (store or office) for a network."""
    try:
        data = request.get_json()
        site_type = data.get('site_type', 'store')
        if site_type not in ('store', 'office'):
            return jsonify({'success': False, 'message': 'site_type must be "store" or "office"'}), 400

        config = load_config()
        networks = config.get('networks', [])
        network = next((n for n in networks if n.get('id') == network_id), None)
        if not network:
            return jsonify({'success': False, 'message': 'Network not found'}), 404

        network['site_type'] = site_type
        config['networks'] = networks
        save_config(config)
        return jsonify({'success': True, 'site_type': site_type})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/logo', methods=['POST'])
def upload_logo():
    """Upload a custom logo (PNG/SVG/WebP/GIF with transparency support)."""
    try:
        if 'logo' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400

        f = request.files['logo']
        if not f.filename:
            return jsonify({'success': False, 'message': 'No file selected'}), 400

        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_LOGO_EXTENSIONS:
            return jsonify({'success': False, 'message': f'Allowed formats: {", ".join(ALLOWED_LOGO_EXTENSIONS)}'}), 400

        data = f.read()
        if len(data) > MAX_LOGO_SIZE:
            return jsonify({'success': False, 'message': 'File too large (max 2 MB)'}), 400

        # Remove any previous logo
        for old in os.listdir(LOGO_DIR):
            if old.startswith('logo.'):
                os.remove(os.path.join(LOGO_DIR, old))

        dest = os.path.join(LOGO_DIR, f'logo.{ext}')
        with open(dest, 'wb') as out:
            out.write(data)

        return jsonify({'success': True, 'url': f'/api/admin/logo?t={int(time.time())}'})
    except Exception as e:
        logging.error("Logo upload error: %s", e)
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/logo', methods=['GET'])
def serve_logo():
    """Serve the uploaded logo file."""
    try:
        for ext in ALLOWED_LOGO_EXTENSIONS:
            path = os.path.join(LOGO_DIR, f'logo.{ext}')
            if os.path.exists(path):
                mime = {
                    'png': 'image/png', 'svg': 'image/svg+xml',
                    'webp': 'image/webp', 'gif': 'image/gif',
                }[ext]
                with open(path, 'rb') as f:
                    return Response(f.read(), mimetype=mime)
        return '', 204  # No logo uploaded
    except Exception as e:
        logging.error("Logo serve error: %s", e)
        return '', 204


@app.route('/api/admin/logo', methods=['DELETE'])
def delete_logo():
    """Remove the custom logo."""
    try:
        for old in os.listdir(LOGO_DIR):
            if old.startswith('logo.'):
                os.remove(os.path.join(LOGO_DIR, old))
        return jsonify({'success': True, 'message': 'Logo removed'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/admin/logs', methods=['GET'])
def get_system_logs():
    """Return the last N lines of the dashboard log file."""
    try:
        lines = int(request.args.get('lines', 100))
        lines = max(1, min(lines, 1000))
        log_file = os.path.join(log_dir, 'dashboard.log')
        if not os.path.exists(log_file):
            return jsonify({'logs': [], 'total_lines': 0})
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return jsonify({'logs': [l.rstrip() for l in tail], 'total_lines': len(all_lines)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_network_logs(network_id):
    """Return eero device activity logs for a specific network with a plain-English summary."""
    try:
        activity = eero_api.get_network_activity(network_id)

        if activity is None:
            # Fallback: return local dashboard logs filtered to this network
            log_file = os.path.join(log_dir, 'dashboard.log')
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    all_lines = f.readlines()
                matched = [l.rstrip() for l in all_lines if network_id in l]
                tail = matched[-50:]
                summary = _summarize_network_logs(network_id, matched)
                return jsonify({
                    'source': 'dashboard',
                    'logs': tail,
                    'summary': summary,
                    'total_matched': len(matched)
                })
            return jsonify({'source': 'none', 'logs': [], 'summary': 'No activity data available from eero API or local logs.'})

        # activity could be a list of events or a dict with entries
        events = []
        if isinstance(activity, list):
            events = activity
        elif isinstance(activity, dict):
            events = activity.get('activities', activity.get('events', activity.get('entries', [])))
            if not isinstance(events, list):
                events = [activity]

        # Format events into readable log lines
        log_lines = []
        for event in events:
            if isinstance(event, dict):
                ts = event.get('timestamp', event.get('created_at', event.get('time', '')))
                msg = event.get('message', event.get('description', event.get('title', '')))
                etype = event.get('type', event.get('category', event.get('event_type', '')))
                source = event.get('source', event.get('actor', ''))
                line_parts = []
                if ts:
                    line_parts.append(str(ts))
                if etype:
                    line_parts.append(f'[{etype}]')
                if source:
                    line_parts.append(f'({source})')
                if msg:
                    line_parts.append(str(msg))
                if line_parts:
                    log_lines.append(' '.join(line_parts))
                else:
                    log_lines.append(json.dumps(event))
            else:
                log_lines.append(str(event))

        summary = _summarize_eero_activity(network_id, events)

        return jsonify({
            'source': 'eero',
            'logs': log_lines[-100:],
            'summary': summary,
            'total_matched': len(log_lines)
        })
    except Exception as e:
        logging.error("Network logs error for %s: %s", network_id, e)
        return jsonify({'error': str(e)}), 500


def _summarize_network_logs(network_id, log_lines):
    """Analyze log lines for a network and produce a human-readable summary."""
    if not log_lines:
        return 'No log activity found for this network.'

    errors = [l for l in log_lines if 'ERROR' in l]
    warnings = [l for l in log_lines if 'WARNING' in l]
    timeouts = [l for l in log_lines if 'timeout' in l.lower() or 'timed out' in l.lower()]
    offline_events = [l for l in log_lines if 'offline' in l.lower()]
    device_fetches = [l for l in log_lines if 'Retrieved' in l and 'devices' in l]
    geocoding = [l for l in log_lines if 'geocod' in l.lower()]

    parts = []

    # Overall activity
    parts.append(f'Found {len(log_lines)} log entries for this network.')

    # Device retrieval
    if device_fetches:
        last_fetch = device_fetches[-1]
        # Extract device count from "Retrieved X devices"
        import re
        m = re.search(r'Retrieved (\d+) devices', last_fetch)
        if m:
            parts.append(f'Last successful device scan retrieved {m.group(1)} devices.')

    # Errors
    if errors:
        parts.append(f'{len(errors)} error(s) recorded.')
        # Show the most recent error context
        last_err = errors[-1]
        if 'fetch' in last_err.lower():
            parts.append('The most recent error was related to fetching data from the eero API.')
        elif 'token' in last_err.lower():
            parts.append('The most recent error was related to authentication tokens.')
        elif 'database' in last_err.lower() or 'db' in last_err.lower():
            parts.append('The most recent error was related to the database.')
        else:
            parts.append('Check the raw logs below for error details.')

    # Warnings
    if warnings:
        parts.append(f'{len(warnings)} warning(s) recorded.')

    # Timeouts
    if timeouts:
        parts.append(f'{len(timeouts)} timeout event(s) detected — the eero API may be slow or unreachable.')

    # Offline events
    if offline_events:
        parts.append(f'{len(offline_events)} offline-related event(s) found. The network may have experienced connectivity issues.')

    # Geocoding
    if geocoding:
        parts.append('Address geocoding activity detected.')

    # If everything looks clean
    if not errors and not warnings and not timeouts and not offline_events:
        parts.append('No errors or warnings — the network appears to be operating normally.')

    return ' '.join(parts)

def _summarize_eero_activity(network_id, events):
    """Produce a human-readable summary from eero device activity events."""
    if not events:
        return 'No recent activity reported by the eero network.'

    parts = [f'{len(events)} event(s) reported by the eero network.']

    # Categorize events
    device_joins = []
    device_leaves = []
    reboots = []
    firmware = []
    speed_tests = []
    connectivity = []
    other = []

    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = str(ev.get('type', ev.get('category', ev.get('event_type', '')))).lower()
        msg = str(ev.get('message', ev.get('description', ev.get('title', '')))).lower()
        combined = etype + ' ' + msg

        if any(k in combined for k in ['join', 'connect', 'new device', 'first seen']):
            device_joins.append(ev)
        elif any(k in combined for k in ['leave', 'disconnect', 'removed']):
            device_leaves.append(ev)
        elif any(k in combined for k in ['reboot', 'restart', 'power cycle']):
            reboots.append(ev)
        elif any(k in combined for k in ['firmware', 'update', 'upgrade']):
            firmware.append(ev)
        elif any(k in combined for k in ['speed', 'bandwidth', 'test']):
            speed_tests.append(ev)
        elif any(k in combined for k in ['offline', 'online', 'connectivity', 'internet', 'wan', 'isp']):
            connectivity.append(ev)
        else:
            other.append(ev)

    if device_joins:
        parts.append(f'{len(device_joins)} device(s) joined the network.')
    if device_leaves:
        parts.append(f'{len(device_leaves)} device(s) left the network.')
    if reboots:
        parts.append(f'{len(reboots)} reboot event(s) detected — an eero node may have restarted.')
    if firmware:
        parts.append(f'{len(firmware)} firmware/update event(s) — eero nodes may have received updates.')
    if speed_tests:
        parts.append(f'{len(speed_tests)} speed test(s) recorded.')
    if connectivity:
        parts.append(f'{len(connectivity)} connectivity event(s) — there may have been internet disruptions.')

    if not any([device_joins, device_leaves, reboots, firmware, speed_tests, connectivity]):
        parts.append('Activity appears routine with no notable issues.')

    return ' '.join(parts)





@app.route('/api/admin/networks/<network_id>/address', methods=['GET'])
def get_network_address(network_id):
    """Get the physical address and coordinates for a network."""
    try:
        config = load_config()
        networks = config.get('networks', [])
        network = next((n for n in networks if n.get('id') == network_id), None)

        if not network:
            return jsonify({'success': False, 'message': 'Network not found'}), 404

        address = network.get('address', {})
        return jsonify({'address': address})

    except Exception as e:
        logging.error("Address fetch error for %s: %s", network_id, str(e))
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/admin/timezone', methods=['POST'])
def change_timezone():
    """Update the dashboard timezone."""
    try:
        data = request.get_json()
        new_timezone = data.get('timezone', '').strip()

        try:
            pytz.timezone(new_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            return jsonify({'success': False, 'message': 'Invalid timezone'}), 400

        config = load_config()
        config['timezone'] = new_timezone

        if save_config(config):
            return jsonify({'success': True, 'message': f'Timezone updated to {new_timezone}'})

        return jsonify({'success': False, 'message': 'Failed to save configuration'}), 500

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Alert API Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/alerts')
def api_get_alerts():
    """Return recent alerts, optionally filtered by network_id."""
    network_id = request.args.get('network_id')
    limit = int(request.args.get('limit', 50))
    alerts = get_recent_alerts(limit=limit, network_id=network_id)
    unack = get_unacknowledged_count()
    return jsonify({'alerts': alerts, 'unacknowledged_count': unack})


@app.route('/api/alerts/<int:alert_id>/acknowledge', methods=['POST'])
def api_acknowledge_alert(alert_id):
    """Acknowledge an alert by ID."""
    success = ack_alert(alert_id)
    if success:
        return jsonify({'success': True, 'message': 'Alert acknowledged'})
    return jsonify({'success': False, 'message': 'Alert not found'}), 404


@app.route('/api/uptime/<network_id>')
def api_get_uptime(network_id):
    """Return uptime metrics for a network across multiple time periods."""
    try:
        network_cache = data_cache.get('networks', {}).get(network_id, {})
        history = network_cache.get('connected_users', [])

        def calc_uptime(points):
            if not points:
                return 100.0
            online = sum(1 for p in points if p.get('count', 0) > 0)
            return round((online / len(points)) * 100, 1)

        # 168 data points = 168 minutes at 1/min, or ~2.8 hours
        # For longer periods, we'd need more historical data
        uptime_24h = network_cache.get('uptime_24h', 100.0)

        return jsonify({
            'network_id': network_id,
            'uptime_24h': uptime_24h,
            'uptime_current': calc_uptime(history[-60:]) if len(history) >= 60 else uptime_24h,
            'data_points': len(history),
            'health_status': network_cache.get('health_status', 'unknown'),
        })
    except Exception as e:
        logging.error("Uptime API error: %s", e)
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Report Generation Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/reports')
def api_get_report():
    """Return report data as JSON."""
    report = generate_report_data(data_cache)
    return jsonify(report)


@app.route('/api/reports/csv')
def api_export_csv():
    """Export report as CSV download."""
    report = generate_report_data(data_cache)
    csv_content = generate_csv(report)
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=eero_dashboard_report.csv'}
    )


@app.route('/api/reports/pdf')
def api_export_pdf():
    """Export report as PDF download."""
    report = generate_report_data(data_cache)
    pdf_bytes = generate_pdf(report)
    if not pdf_bytes:
        return jsonify({'error': 'PDF generation unavailable — install reportlab'}), 500
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': 'attachment; filename=eero_dashboard_report.pdf'}
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Weather API (Open-Meteo — free, no API key)
# ---------------------------------------------------------------------------

_weather_cache = {}  # { "lat,lng": { data, fetched_at } }
WEATHER_CACHE_TTL = 1800  # 30 minutes

# WMO weather code to description + icon mapping
_WMO_CODES = {
    0: ('Clear', '☀️'),
    1: ('Mostly Clear', '🌤️'),
    2: ('Partly Cloudy', '⛅'),
    3: ('Overcast', '☁️'),
    45: ('Fog', '🌫️'),
    48: ('Fog', '🌫️'),
    51: ('Light Drizzle', '🌦️'),
    53: ('Drizzle', '🌦️'),
    55: ('Heavy Drizzle', '🌧️'),
    56: ('Freezing Drizzle', '🌧️'),
    57: ('Freezing Drizzle', '🌧️'),
    61: ('Light Rain', '🌧️'),
    63: ('Rain', '🌧️'),
    65: ('Heavy Rain', '🌧️'),
    66: ('Freezing Rain', '🌧️'),
    67: ('Freezing Rain', '🌧️'),
    71: ('Light Snow', '🌨️'),
    73: ('Snow', '❄️'),
    75: ('Heavy Snow', '❄️'),
    77: ('Snow Grains', '❄️'),
    80: ('Light Showers', '🌦️'),
    81: ('Showers', '🌧️'),
    82: ('Heavy Showers', '🌧️'),
    85: ('Snow Showers', '🌨️'),
    86: ('Heavy Snow Showers', '❄️'),
    95: ('Thunderstorm', '⛈️'),
    96: ('Thunderstorm w/ Hail', '⛈️'),
    99: ('Thunderstorm w/ Hail', '⛈️'),
}


@app.route('/api/weather')
def get_weather():
    """Get current weather for all network locations."""
    try:
        config = load_config()
        networks = config.get('networks', [])
        results = {}
        now = time.time()

        for network in networks:
            network_id = network.get('id')
            address = network.get('address', {})
            lat = address.get('lat')
            lng = address.get('lng')
            if not lat or not lng:
                continue

            cache_key = f"{round(lat, 2)},{round(lng, 2)}"

            # Check cache
            cached = _weather_cache.get(cache_key)
            if cached and (now - cached['fetched_at']) < WEATHER_CACHE_TTL:
                results[network_id] = cached['data']
                continue

            # Fetch from Open-Meteo
            try:
                url = (
                    f"https://api.open-meteo.com/v1/forecast"
                    f"?latitude={lat}&longitude={lng}"
                    f"&current=temperature_2m,weather_code,is_day"
                    f"&temperature_unit=fahrenheit"
                    f"&timezone=auto"
                )
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    weather_data = resp.json().get('current', {})
                    temp = weather_data.get('temperature_2m')
                    code = weather_data.get('weather_code', 0)
                    is_day = weather_data.get('is_day', 1)
                    desc, icon = _WMO_CODES.get(code, ('Unknown', 'fa-question'))

                    # Use moon icon at night for clear/mostly clear
                    if not is_day and code in (0, 1):
                        icon = '🌙'

                    result = {
                        'temp_f': round(temp) if temp is not None else None,
                        'description': desc,
                        'icon': icon,
                        'code': code,
                    }
                    _weather_cache[cache_key] = {'data': result, 'fetched_at': now}
                    results[network_id] = result
                else:
                    logging.warning("Weather API returned %d for %s", resp.status_code, cache_key)
            except Exception as e:
                logging.warning("Weather fetch error for %s: %s", cache_key, e)

        return jsonify(results)
    except Exception as e:
        logging.error("Weather endpoint error: %s", e)
        return jsonify({}), 500

# ---------------------------------------------------------------------------
# Traffic Conditions API (TomTom Flow Segment Data)
# ---------------------------------------------------------------------------

_traffic_cache = {}  # { "lat,lng": { data, fetched_at } }
TRAFFIC_CACHE_TTL = 300  # 5 minutes
TRAFFIC_HISTORY_MAX = 25  # ~2 hours at 5-min intervals + 1 buffer


def _get_tomtom_key():
    """Get TomTom API key from config or environment."""
    config = load_config()
    key = config.get('tomtom_api_key', '')
    if not key:
        key = os.environ.get('TOMTOM_API_KEY', '')
    return key


@app.route('/api/admin/tomtom', methods=['GET'])
def get_tomtom_key_status():
    """Check if TomTom API key is configured."""
    key = _get_tomtom_key()
    return jsonify({'configured': bool(key), 'key_preview': key[:4] + '...' if key else ''})


@app.route('/api/admin/tomtom', methods=['POST'])
def save_tomtom_key():
    """Save TomTom API key to config."""
    data = request.get_json()
    key = data.get('key', '').strip()
    config = load_config()
    config['tomtom_api_key'] = key
    save_config(config)
    _traffic_cache.clear()
    return jsonify({'success': True, 'configured': bool(key)})


@app.route('/api/traffic')
def get_traffic():
    """Get real-time traffic conditions near each network location.

    Uses TomTom Flow Segment Data API to get current speed vs free-flow
    speed for the nearest road segment to each location.
    """
    tomtom_key = _get_tomtom_key()
    if not tomtom_key:
        return jsonify({'_no_key': True}), 200

    try:
        config = load_config()
        networks = config.get('networks', [])
        results = {}
        now = time.time()

        for network in networks:
            network_id = network.get('id')
            address = network.get('address', {})
            lat = address.get('lat')
            lng = address.get('lng')
            if not lat or not lng:
                continue

            cache_key = f"{round(lat, 4)},{round(lng, 4)}"

            # Check cache
            cached = _traffic_cache.get(cache_key)
            if cached and (now - cached['fetched_at']) < TRAFFIC_CACHE_TTL:
                results[network_id] = cached['data']
                continue

            # Fetch from TomTom
            try:
                url = (
                    f"https://api.tomtom.com/traffic/services/4/flowSegmentData"
                    f"/absolute/10/json"
                    f"?key={tomtom_key}"
                    f"&point={lat},{lng}"
                    f"&unit=mph"
                )
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    flow = resp.json().get('flowSegmentData', {})
                    current_speed = flow.get('currentSpeed', 0)
                    free_flow = flow.get('freeFlowSpeed', 0)
                    confidence = flow.get('confidence', 0)
                    road_closure = flow.get('roadClosure', False)
                    frc = flow.get('frc', '')

                    # Calculate congestion ratio
                    if free_flow > 0:
                        ratio = current_speed / free_flow
                    else:
                        ratio = 1.0

                    # Determine condition label and color
                    if road_closure:
                        condition = 'Road Closed'
                        color = '#F44336'
                        icon = '🚫'
                    elif ratio >= 0.85:
                        condition = 'Clear'
                        color = '#4CAF50'
                        icon = '🟢'
                    elif ratio >= 0.65:
                        condition = 'Moderate'
                        color = '#FFC107'
                        icon = '🟡'
                    elif ratio >= 0.40:
                        condition = 'Heavy'
                        color = '#FF9800'
                        icon = '🟠'
                    else:
                        condition = 'Severe'
                        color = '#F44336'
                        icon = '🔴'

                    result = {
                        'current_speed_mph': round(current_speed),
                        'free_flow_speed_mph': round(free_flow),
                        'ratio': round(ratio, 2),
                        'condition': condition,
                        'color': color,
                        'icon': icon,
                        'confidence': round(confidence, 2),
                        'road_closure': road_closure,
                    }
                    _traffic_cache[cache_key] = {'data': result, 'fetched_at': now}
                    results[network_id] = result

                    # Record traffic history snapshot for timeline overlay
                    ts_iso = get_timezone_aware_now().isoformat()
                    snapshot = {
                        'timestamp': ts_iso,
                        'ratio': round(ratio, 2),
                        'condition': condition,
                        'icon': icon,
                        'color': color,
                        'current_speed_mph': round(current_speed),
                        'free_flow_speed_mph': round(free_flow),
                    }
                    if network_id not in _traffic_history:
                        _traffic_history[network_id] = []
                    hist = _traffic_history[network_id]
                    # Only append if last entry is > 4 minutes old (avoid duplicates)
                    should_append = True
                    if hist:
                        try:
                            last_ts = datetime.fromisoformat(hist[-1]['timestamp'].replace('Z', '+00:00'))
                            if last_ts.tzinfo is None:
                                last_ts = pytz.UTC.localize(last_ts)
                            should_append = (now - last_ts.timestamp()) > 240
                        except (ValueError, TypeError):
                            pass
                    if should_append:
                        hist.append(snapshot)
                        if len(hist) > TRAFFIC_HISTORY_MAX:
                            _traffic_history[network_id] = hist[-TRAFFIC_HISTORY_MAX:]
                else:
                    logging.warning("TomTom API returned %d for %s", resp.status_code, cache_key)
            except Exception as e:
                logging.warning("Traffic fetch error for %s: %s", cache_key, e)

        return jsonify(results)
    except Exception as e:
        logging.error("Traffic endpoint error: %s", e)
        return jsonify({}), 500


@app.route('/api/store-activity')
def get_store_activity():
    """Get per-network device count history and current busyness level.

    Returns 2 hours of device count data bucketed into 10-minute intervals
    (12 data points) for the sparkline, plus a busyness indicator comparing
    current count to historical average.  Also includes traffic history
    snapshots aligned to the same 10-minute buckets when available.
    """
    try:
        config = load_config()
        networks = config.get('networks', [])
        results = {}
        now = get_timezone_aware_now()
        two_hours_ago = now - timedelta(hours=2)

        for network in networks:
            network_id = network.get('id')
            if not network_id or network_id not in data_cache.get('networks', {}):
                continue

            nc = data_cache['networks'][network_id]
            history = nc.get('connected_users', [])

            # Build 24 five-minute buckets covering the last 2 hours
            # Use wireless_count (wifi devices only) for store activity
            bucket_labels = []
            bucket_counts = []
            for i in range(24):
                bucket_start = two_hours_ago + timedelta(minutes=i * 5)
                bucket_end = bucket_start + timedelta(minutes=5)
                bucket_labels.append(bucket_start.strftime('%H:%M'))

                # Average all data points that fall within this bucket
                bucket_values = []
                for p in history:
                    try:
                        ts = datetime.fromisoformat(p['timestamp'].replace('Z', '+00:00'))
                        if ts.tzinfo is None:
                            ts = pytz.UTC.localize(ts)
                        ts_local = ts.astimezone(now.tzinfo)
                        if bucket_start <= ts_local < bucket_end:
                            bucket_values.append(p.get('wireless_count', p.get('count', 0)))
                    except (ValueError, TypeError, KeyError):
                        continue

                if bucket_values:
                    bucket_counts.append(round(sum(bucket_values) / len(bucket_values), 1))
                else:
                    bucket_counts.append(0)

            current = bucket_counts[-1] if bucket_counts else 0

            # Calculate average and peak from all history (wireless only)
            all_counts = [p.get('wireless_count', p.get('count', 0)) for p in history]
            avg = sum(all_counts) / len(all_counts) if all_counts else 0
            peak = max(all_counts) if all_counts else 0

            # Busyness level
            if avg == 0:
                level = 'No Data'
                level_icon = '⚪'
                level_color = '#868e96'
            elif current <= avg * 0.5:
                level = 'Quiet'
                level_icon = '🟢'
                level_color = '#4CAF50'
            elif current <= avg * 1.2:
                level = 'Normal'
                level_icon = '🔵'
                level_color = '#4da6ff'
            elif current <= avg * 1.8:
                level = 'Busy'
                level_icon = '🟡'
                level_color = '#FFC107'
            else:
                level = 'Peak'
                level_icon = '🔴'
                level_color = '#F44336'

            # Include traffic history aligned to buckets
            traffic_hist = _traffic_history.get(network_id, [])
            traffic_timeline = []
            for i in range(24):
                bucket_start = two_hours_ago + timedelta(minutes=i * 5)
                bucket_end = bucket_start + timedelta(minutes=5)
                matched = None
                for th in traffic_hist:
                    try:
                        ts = datetime.fromisoformat(th['timestamp'].replace('Z', '+00:00'))
                        if ts.tzinfo is None:
                            ts = pytz.UTC.localize(ts)
                        ts_local = ts.astimezone(now.tzinfo)
                        if bucket_start <= ts_local < bucket_end:
                            matched = th
                    except (ValueError, TypeError):
                        continue
                traffic_timeline.append(matched)

            results[network_id] = {
                'name': network.get('name', f'Network {network_id}'),
                'current': current,
                'average': round(avg, 1),
                'peak': peak,
                'level': level,
                'level_icon': level_icon,
                'level_color': level_color,
                'sparkline_counts': bucket_counts,
                'sparkline_labels': bucket_labels,
                'traffic_timeline': traffic_timeline,
            }

        return jsonify(results)
    except Exception as e:
        logging.error("Store activity endpoint error: %s", e)
        return jsonify({}), 500

# ---------------------------------------------------------------------------
# Insights API Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/insights/heatmap')
def api_insights_heatmap():
    """Return 7x24 heatmap data aggregated from metrics database.

    Queries the metrics table for the last 7 days, groups total_devices
    by day-of-week (0=Monday..6=Sunday) and hour-of-day (0-23), and
    returns average device counts per cell.  Office networks are excluded
    so the heatmap reflects store/restaurant traffic only.
    """
    try:
        from app.database import get_db_session, Metric

        now = get_timezone_aware_now()
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        # Determine which network IDs are stores (exclude office)
        config = load_config()
        store_ids = [
            str(n.get('id', ''))
            for n in config.get('networks', [])
            if n.get('site_type', 'store') != 'office'
        ]

        # Query metrics from the last 7 days for store networks only
        with get_db_session() as session:
            query = (
                session.query(Metric.timestamp, Metric.total_devices)
                .filter(Metric.timestamp >= seven_days_ago)
                .filter(Metric.total_devices.isnot(None))
            )
            if store_ids:
                query = query.filter(Metric.network_id.in_(store_ids))
            rows = query.all()

        # Accumulate sums and counts per (day_of_week, hour) bucket
        sums = [[0.0] * 24 for _ in range(7)]
        counts = [[0] * 24 for _ in range(7)]

        for ts_str, total_devices in rows:
            try:
                dt = datetime.fromisoformat(ts_str)
                day = dt.weekday()   # 0=Monday .. 6=Sunday
                hour = dt.hour
                sums[day][hour] += total_devices
                counts[day][hour] += 1
            except (ValueError, TypeError):
                continue

        # Compute averages
        cells = []
        max_value = 0.0
        for day in range(7):
            row = []
            for hour in range(24):
                if counts[day][hour] > 0:
                    avg = round(sums[day][hour] / counts[day][hour], 1)
                else:
                    avg = 0.0
                row.append(avg)
                if avg > max_value:
                    max_value = avg
            cells.append(row)

        return jsonify({
            'cells': cells,
            'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            'hours': list(range(24)),
            'max_value': max_value,
        })

    except Exception as e:
        logging.error("Heatmap API error: %s", e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/insights/uptime-timeline')
def api_insights_uptime_timeline():
    """Return per-network online/offline segments for the last 24 hours.

    For each network in the config, merges connected_users time-series
    from data_cache with uptime_incidents from the database to produce
    contiguous segments covering exactly 24 hours.
    """
    try:
        from app.database import get_db_session, UptimeIncident

        config = load_config()
        networks = config.get('networks', [])
        now = get_timezone_aware_now()
        twenty_four_hours_ago = now - timedelta(hours=24)

        result = {}

        for network in networks:
            network_id = str(network.get('id', ''))
            network_name = network.get('name', f'Network {network_id}')

            if not network_id:
                continue

            # Query uptime incidents from the last 24 hours
            with get_db_session() as session:
                incidents = (
                    session.query(UptimeIncident)
                    .filter(UptimeIncident.network_id == network_id)
                    .filter(
                        UptimeIncident.start_time >= twenty_four_hours_ago.isoformat()
                    )
                    .order_by(UptimeIncident.start_time.asc())
                    .all()
                )
                # Also include incidents that started before the window but
                # haven't ended yet (or ended within the window)
                ongoing = (
                    session.query(UptimeIncident)
                    .filter(UptimeIncident.network_id == network_id)
                    .filter(
                        UptimeIncident.start_time < twenty_four_hours_ago.isoformat()
                    )
                    .filter(
                        (UptimeIncident.end_time.is_(None))
                        | (UptimeIncident.end_time >= twenty_four_hours_ago.isoformat())
                    )
                    .order_by(UptimeIncident.start_time.asc())
                    .all()
                )
                session.expunge_all()

            all_incidents = ongoing + incidents

            if not all_incidents:
                # No incidents — single online segment spanning 24 hours
                result[network_id] = {
                    'name': network_name,
                    'segments': [{
                        'start': twenty_four_hours_ago.isoformat(),
                        'end': now.isoformat(),
                        'status': 'online',
                    }],
                }
                continue

            # Check current health from cache to handle orphaned incidents
            net_cache = data_cache.get('networks', {}).get(network_id, {})
            current_health = net_cache.get('health_status', '')
            network_is_online = current_health and current_health != 'offline'

            # Build offline intervals from incidents, clamped to the 24h window
            configured_tz = pytz.timezone(config.get('timezone', 'UTC'))
            offline_intervals = []
            for inc in all_incidents:
                try:
                    inc_start = datetime.fromisoformat(inc.start_time)
                    if inc_start.tzinfo is None:
                        inc_start = configured_tz.localize(inc_start)
                except (ValueError, TypeError):
                    continue

                if inc.end_time:
                    try:
                        inc_end = datetime.fromisoformat(inc.end_time)
                        if inc_end.tzinfo is None:
                            inc_end = configured_tz.localize(inc_end)
                    except (ValueError, TypeError):
                        inc_end = now
                else:
                    # Ongoing incident — but if the network is currently online
                    # in the cache, treat the incident as ended at the last
                    # successful update time (or now minus the refresh interval)
                    # to avoid showing a false offline segment.
                    if network_is_online:
                        last_update = net_cache.get('last_successful_update')
                        if last_update:
                            try:
                                inc_end = datetime.fromisoformat(last_update)
                                if inc_end.tzinfo is None:
                                    inc_end = configured_tz.localize(inc_end)
                            except (ValueError, TypeError):
                                inc_end = now - timedelta(seconds=REFRESH_INTERVAL)
                        else:
                            inc_end = now - timedelta(seconds=REFRESH_INTERVAL)
                    else:
                        inc_end = now

                # Clamp to the 24-hour window
                clamped_start = max(inc_start, twenty_four_hours_ago)
                clamped_end = min(inc_end, now)

                if clamped_start < clamped_end:
                    offline_intervals.append((clamped_start, clamped_end))

            # Merge overlapping offline intervals
            if offline_intervals:
                offline_intervals.sort(key=lambda x: x[0])
                merged = [offline_intervals[0]]
                for start, end in offline_intervals[1:]:
                    if start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                    else:
                        merged.append((start, end))
                offline_intervals = merged

            # Build contiguous segments from offline intervals
            segments = []
            cursor = twenty_four_hours_ago

            for off_start, off_end in offline_intervals:
                if cursor < off_start:
                    segments.append({
                        'start': cursor.isoformat(),
                        'end': off_start.isoformat(),
                        'status': 'online',
                    })
                segments.append({
                    'start': off_start.isoformat(),
                    'end': off_end.isoformat(),
                    'status': 'offline',
                })
                cursor = off_end

            # Fill remaining time as online
            if cursor < now:
                segments.append({
                    'start': cursor.isoformat(),
                    'end': now.isoformat(),
                    'status': 'online',
                })

            result[network_id] = {
                'name': network_name,
                'segments': segments,
            }

        return jsonify({'networks': result})

    except Exception as e:
        logging.error("Uptime timeline API error: %s", e)
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/trend')
def api_alerts_trend():
    """Return daily alert counts for the last 7 days.

    Queries the alerts table, groups by date, and returns counts
    in chronological order (oldest first). Returns 7 days of zero
    counts if no alerts exist.
    """
    try:
        from app.database import get_db_session, Alert

        now = get_timezone_aware_now()
        seven_days_ago = now - timedelta(days=7)

        # Build list of the last 7 dates (oldest first)
        date_list = []
        for i in range(7, 0, -1):
            day = now - timedelta(days=i)
            date_list.append(day.strftime('%Y-%m-%d'))

        # Query alerts from the last 7 days
        with get_db_session() as session:
            alerts = (
                session.query(Alert)
                .filter(Alert.created_at >= seven_days_ago.isoformat())
                .all()
            )
            session.expunge_all()

        # Count alerts per day
        day_counts = {d: 0 for d in date_list}
        for alert in alerts:
            try:
                alert_date = datetime.fromisoformat(alert.created_at).strftime('%Y-%m-%d')
                if alert_date in day_counts:
                    day_counts[alert_date] += 1
            except (ValueError, TypeError):
                continue

        counts = [day_counts[d] for d in date_list]

        return jsonify({
            'days': date_list,
            'counts': counts,
        })

    except Exception as e:
        logging.error("Alert trend API error: %s", e)
        return jsonify({'error': str(e)}), 500

@app.route('/api/reports/scorecard')
def api_reports_scorecard():
    """Return letter grades and metric breakdowns for all networks.

    For each network in the config, queries metrics and alerts from the
    last 7 days, computes weighted scores, and assigns a letter grade.
    Returns "N/A" grade if fewer than 24 metric records exist for a network.
    """
    try:
        from app.database import get_db_session, Metric, Alert, UptimeIncident
        from app.computations import compute_scorecard_score, score_to_grade

        config = load_config()
        networks_config = config.get('networks', [])
        now = get_timezone_aware_now()
        seven_days_ago = now - timedelta(days=7)
        seven_days_ago_iso = seven_days_ago.isoformat()

        result_networks = []

        for network in networks_config:
            network_id = str(network.get('id', ''))
            network_name = network.get('name', f'Network {network_id}')

            if not network_id:
                continue

            # Query metrics from the last 7 days
            with get_db_session() as session:
                metrics = (
                    session.query(Metric)
                    .filter(Metric.network_id == network_id)
                    .filter(Metric.timestamp >= seven_days_ago_iso)
                    .order_by(Metric.timestamp.asc())
                    .all()
                )
                session.expunge_all()

            # Check for insufficient data (fewer than 24 metric records)
            if len(metrics) < 24:
                result_networks.append({
                    'id': network_id,
                    'name': network_name,
                    'grade': 'N/A',
                    'score': None,
                    'breakdown': None,
                    'data_days': len(metrics) / 24.0 if metrics else 0,
                    'message': 'Insufficient data',
                })
                continue

            # Compute uptime score from uptime incidents
            with get_db_session() as session:
                incidents = (
                    session.query(UptimeIncident)
                    .filter(UptimeIncident.network_id == network_id)
                    .filter(UptimeIncident.start_time >= seven_days_ago_iso)
                    .all()
                )
                # Also include incidents that started before the window
                ongoing = (
                    session.query(UptimeIncident)
                    .filter(UptimeIncident.network_id == network_id)
                    .filter(UptimeIncident.start_time < seven_days_ago_iso)
                    .filter(
                        (UptimeIncident.end_time.is_(None))
                        | (UptimeIncident.end_time >= seven_days_ago_iso)
                    )
                    .all()
                )
                session.expunge_all()

            all_incidents = ongoing + incidents
            total_seconds = 7 * 24 * 3600  # 7 days in seconds
            total_downtime = 0

            for inc in all_incidents:
                try:
                    inc_start = datetime.fromisoformat(inc.start_time)
                    if inc_start.tzinfo is None:
                        inc_start = pytz.UTC.localize(inc_start)
                except (ValueError, TypeError):
                    continue

                if inc.end_time:
                    try:
                        inc_end = datetime.fromisoformat(inc.end_time)
                        if inc_end.tzinfo is None:
                            inc_end = pytz.UTC.localize(inc_end)
                    except (ValueError, TypeError):
                        inc_end = now
                else:
                    inc_end = now

                # Clamp to the 7-day window
                clamped_start = max(inc_start, seven_days_ago)
                clamped_end = min(inc_end, now)

                if clamped_start < clamped_end:
                    total_downtime += (clamped_end - clamped_start).total_seconds()

            uptime_score = max(0, min(100, ((total_seconds - total_downtime) / total_seconds) * 100))

            # Compute signal score from avg_signal_dbm
            signal_values = [m.avg_signal_dbm for m in metrics if m.avg_signal_dbm is not None]
            if signal_values:
                avg_signal_dbm = sum(signal_values) / len(signal_values)
            else:
                avg_signal_dbm = -90.0  # Worst case default

            signal_score = max(0, min(100, ((avg_signal_dbm + 90) / 60) * 100))

            # Compute incident score from alert count
            with get_db_session() as session:
                alert_count = (
                    session.query(Alert)
                    .filter(Alert.network_id == network_id)
                    .filter(Alert.created_at >= seven_days_ago_iso)
                    .count()
                )

            incident_score = max(0, 100 - alert_count * 10)

            # Compute bandwidth score
            bw_values = [m.bandwidth_utilization for m in metrics if m.bandwidth_utilization is not None]
            if bw_values:
                avg_bandwidth = sum(bw_values) / len(bw_values)
            else:
                avg_bandwidth = 0.0

            bandwidth_score = 100 - avg_bandwidth

            # Compute weighted score and grade
            weighted_score = compute_scorecard_score(
                uptime_score, signal_score, incident_score, bandwidth_score
            )
            grade = score_to_grade(weighted_score)

            # Calculate data days
            if metrics:
                try:
                    first_ts = datetime.fromisoformat(metrics[0].timestamp)
                    last_ts = datetime.fromisoformat(metrics[-1].timestamp)
                    data_days = max(1, round((last_ts - first_ts).total_seconds() / 86400, 1))
                except (ValueError, TypeError):
                    data_days = 7
            else:
                data_days = 0

            result_networks.append({
                'id': network_id,
                'name': network_name,
                'grade': grade,
                'score': round(weighted_score, 1),
                'breakdown': {
                    'uptime': {
                        'value': round(uptime_score, 1),
                        'score': round(uptime_score, 1),
                        'weight': 0.40,
                    },
                    'signal': {
                        'value': round(avg_signal_dbm, 1),
                        'score': round(signal_score, 1),
                        'weight': 0.25,
                    },
                    'incidents': {
                        'count': alert_count,
                        'score': round(incident_score, 1),
                        'weight': 0.20,
                    },
                    'bandwidth': {
                        'utilization': round(avg_bandwidth, 1),
                        'score': round(bandwidth_score, 1),
                        'weight': 0.15,
                    },
                },
                'data_days': data_days,
            })

        return jsonify({'networks': result_networks})

    except Exception as e:
        logging.error("Scorecard API error: %s", e)
        return jsonify({'error': str(e)}), 500





# ---------------------------------------------------------------------------
# Background Cache Refresh
# ---------------------------------------------------------------------------

_refresh_thread = None
_refresh_stop = threading.Event()
_cache_lock = threading.Lock()

REFRESH_INTERVAL = int(os.environ.get('EERO_REFRESH_INTERVAL', '60'))  # seconds


def _close_orphaned_incidents():
    """Close any open uptime incidents for networks that are currently online.

    This handles the case where the app was stopped (or crashed) while a
    network was offline and has since recovered — the incident never got
    its end_time set.
    """
    try:
        from app.database import get_db_session, UptimeIncident
        now = get_timezone_aware_now()
        with get_db_session() as session:
            open_incidents = (
                session.query(UptimeIncident)
                .filter(UptimeIncident.end_time.is_(None))
                .all()
            )
            if not open_incidents:
                return
            for inc in open_incidents:
                network_id = inc.network_id
                # Check if this network is currently healthy in the cache
                net_cache = data_cache.get('networks', {}).get(str(network_id), {})
                health = net_cache.get('health_status', '')
                if health and health != 'offline':
                    inc.end_time = now.isoformat()
                    try:
                        inc_start = datetime.fromisoformat(inc.start_time)
                        if inc_start.tzinfo is None:
                            inc_start = pytz.UTC.localize(inc_start)
                        duration = int((now - inc_start).total_seconds())
                        inc.duration_seconds = max(0, duration)
                    except (ValueError, TypeError):
                        pass
                    logging.info("Closed orphaned incident for network %s (started %s)",
                                 network_id, inc.start_time)
            session.commit()
    except Exception as e:
        logging.error("Failed to close orphaned incidents: %s", e)


def _background_refresh():
    """Background thread that periodically updates the cache."""
    logging.info("Background refresh thread started (interval: %ds)", REFRESH_INTERVAL)
    while not _refresh_stop.is_set():
        _refresh_stop.wait(REFRESH_INTERVAL)
        if _refresh_stop.is_set():
            break
        try:
            update_cache()
            logging.debug("Background cache refresh complete")
        except Exception as e:
            logging.error("Background refresh error: %s", e)


def start_background_refresh():
    """Start the background cache refresh thread."""
    global _refresh_thread
    if _refresh_thread and _refresh_thread.is_alive():
        return
    # Close any orphaned incidents from previous runs
    _close_orphaned_incidents()
    _refresh_stop.clear()
    _refresh_thread = threading.Thread(target=_background_refresh, daemon=True)
    _refresh_thread.start()


def stop_background_refresh():
    """Stop the background cache refresh thread."""
    _refresh_stop.set()
    if _refresh_thread:
        _refresh_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Manual Refresh Endpoint
# ---------------------------------------------------------------------------

@app.route('/api/refresh', methods=['POST'])
def api_manual_refresh():
    """Trigger an immediate cache refresh."""
    if _cache_lock.locked():
        return jsonify({'success': True, 'message': 'Cache refresh already in progress'})
    try:
        update_cache()
        return jsonify({'success': True, 'message': 'Cache refreshed'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    logging.info("Starting eero Business Dashboard %s", VERSION)

    # Initialize database
    try:
        from app.database import init_db
        init_db()
        logging.info("Database initialized")
    except Exception as e:
        logging.warning("Database init failed: %s", str(e))

    # Initial cache update
    try:
        update_cache()
        logging.info("Initial cache update complete")
    except Exception as e:
        logging.warning("Initial cache update failed: %s", str(e))

    # Start background refresh
    start_background_refresh()

    debug = os.environ.get('EERO_ENV', 'development') == 'development'
    port = int(os.environ.get('EERO_PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)
