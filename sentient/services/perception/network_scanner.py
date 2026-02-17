"""
Network Intelligence Scanner for Sentient Core Perception Service

Scans the local network by reading /proc/net/arp to discover devices.
Tracks device arrivals, departures, and maintains a known device registry.
Zero dependencies beyond stdlib + redis.
"""

import asyncio
import json
import logging
import socket
import subprocess
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Set
from datetime import datetime

import redis.asyncio as redis

logger = logging.getLogger("perception.network")


@dataclass
class NetworkDevice:
    """Represents a device on the network"""
    mac: str
    ip: str
    hostname: Optional[str] = None
    name: Optional[str] = None  # User-friendly name (from known devices registry)
    known: bool = False
    first_seen: str = ""
    last_seen: str = ""
    is_gateway: bool = False


# Known devices registry - maps MAC address to friendly name
# Jack can populate this via Redis or config
KNOWN_DEVICES: Dict[str, str] = {
    # These will be loaded from Redis key "sentient:network:known_devices"
    # Format: {"aa:bb:cc:dd:ee:ff": "Jack's Phone", ...}
}


class NetworkScanner:
    """
    Scans local network using /proc/net/arp.

    This is extremely lightweight - no nmap, no raw sockets, no root needed.
    Just reads the kernel's ARP table which contains all recently-communicating devices.
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None, scan_interval: float = 30.0):
        self.redis_client = redis_client
        self.scan_interval = scan_interval

        # Current state
        self.devices: Dict[str, NetworkDevice] = {}  # MAC -> device
        self.previous_macs: Set[str] = set()

        # Events
        self.arrivals: List[NetworkDevice] = []
        self.departures: List[NetworkDevice] = []

        # Known devices loaded from Redis
        self.known_devices: Dict[str, str] = {}

        # Gateway detection
        self.gateway_ip: Optional[str] = None
        self.gateway_mac: Optional[str] = None

        # Stats
        self.last_scan_time: float = 0
        self.scan_count: int = 0

        logger.info("NetworkScanner initialized")

    async def initialize(self):
        """Load known devices from Redis and detect gateway"""
        # Detect gateway
        try:
            result = subprocess.run(
                ['ip', 'route', 'show', 'default'],
                capture_output=True, text=True, timeout=5
            )
            # Output like: "default via 192.168.1.1 dev eth0 proto ..."
            parts = result.stdout.strip().split()
            if 'via' in parts:
                self.gateway_ip = parts[parts.index('via') + 1]
                logger.info(f"Gateway detected: {self.gateway_ip}")
        except Exception as e:
            logger.warning(f"Could not detect gateway: {e}")

        # Load known devices from Redis
        if self.redis_client:
            try:
                raw = await self.redis_client.get("sentient:network:known_devices")
                if raw:
                    self.known_devices = json.loads(raw)
                    logger.info(f"Loaded {len(self.known_devices)} known devices from Redis")
            except Exception as e:
                logger.warning(f"Could not load known devices: {e}")

    def _read_arp_table(self) -> List[Dict[str, str]]:
        """Read /proc/net/arp to get current ARP entries"""
        devices = []
        try:
            with open('/proc/net/arp', 'r') as f:
                lines = f.readlines()

            # Skip header line
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 6:
                    ip = parts[0]
                    # HW type 0x1 = ethernet
                    hw_type = parts[1]
                    # Flags: 0x2 = complete entry, 0x0 = incomplete
                    flags = parts[2]
                    mac = parts[3]

                    # Skip incomplete entries and broadcast
                    if mac == '00:00:00:00:00:00' or flags == '0x0':
                        continue

                    devices.append({
                        'ip': ip,
                        'mac': mac.lower(),
                        'interface': parts[5] if len(parts) > 5 else 'unknown'
                    })
        except Exception as e:
            logger.error(f"Error reading ARP table: {e}")

        return devices

    def _resolve_hostname(self, ip: str) -> Optional[str]:
        """Try to resolve hostname for an IP (with timeout)"""
        try:
            hostname = socket.getfqdn(ip)
            # getfqdn returns the IP if resolution fails
            if hostname != ip and not hostname.startswith('0.'):
                return hostname
        except Exception:
            pass
        return None

    async def scan(self) -> Dict[str, any]:
        """
        Perform a network scan and return results.

        Returns dict with:
        - devices: list of NetworkDevice dicts
        - arrivals: list of newly seen devices
        - departures: list of devices no longer seen
        - device_count: total devices
        - known_count: known devices
        - unknown_count: unknown devices
        """
        now = datetime.now().isoformat()
        arp_entries = self._read_arp_table()

        current_macs = set()
        self.arrivals = []
        self.departures = []

        for entry in arp_entries:
            mac = entry['mac']
            ip = entry['ip']
            current_macs.add(mac)

            is_gateway = (ip == self.gateway_ip)
            is_known = mac in self.known_devices
            friendly_name = self.known_devices.get(mac)

            if mac in self.devices:
                # Update existing device
                device = self.devices[mac]
                device.ip = ip
                device.last_seen = now
                device.known = is_known
                device.name = friendly_name
                device.is_gateway = is_gateway
            else:
                # New device - try hostname resolution in executor to not block
                hostname = None
                try:
                    hostname = await asyncio.get_event_loop().run_in_executor(
                        None, self._resolve_hostname, ip
                    )
                except Exception:
                    pass

                device = NetworkDevice(
                    mac=mac,
                    ip=ip,
                    hostname=hostname,
                    name=friendly_name,
                    known=is_known,
                    first_seen=now,
                    last_seen=now,
                    is_gateway=is_gateway
                )
                self.devices[mac] = device

                # Only count as arrival if we've done at least one scan before
                if self.scan_count > 0:
                    self.arrivals.append(device)
                    logger.info(f"NEW DEVICE: {device.name or device.hostname or device.mac} ({device.ip})")

        # Detect departures (devices in previous but not current)
        departed_macs = self.previous_macs - current_macs
        for mac in departed_macs:
            if mac in self.devices:
                device = self.devices[mac]
                self.departures.append(device)
                logger.info(f"DEVICE LEFT: {device.name or device.hostname or device.mac} ({device.ip})")
                # Don't remove immediately - keep for history
                # But mark as stale after 5 minutes

        # Clean up devices not seen for 5+ minutes
        stale_cutoff = time.time() - 300
        stale_macs = []
        for mac, device in self.devices.items():
            if mac not in current_macs:
                try:
                    last_seen_ts = datetime.fromisoformat(device.last_seen).timestamp()
                    if last_seen_ts < stale_cutoff:
                        stale_macs.append(mac)
                except Exception:
                    pass
        for mac in stale_macs:
            del self.devices[mac]

        self.previous_macs = current_macs
        self.last_scan_time = time.time()
        self.scan_count += 1

        # Store network state in Redis
        if self.redis_client:
            try:
                state = {
                    'devices': [asdict(d) for d in self.devices.values()],
                    'device_count': len(self.devices),
                    'known_count': sum(1 for d in self.devices.values() if d.known),
                    'unknown_count': sum(1 for d in self.devices.values() if not d.known),
                    'gateway_ip': self.gateway_ip,
                    'last_scan': now,
                    'scan_count': self.scan_count
                }
                await self.redis_client.set(
                    "sentient:network:state",
                    json.dumps(state),
                    ex=120  # Expire after 2 minutes if not refreshed
                )
            except Exception as e:
                logger.warning(f"Could not store network state in Redis: {e}")

        # Build result
        device_list = sorted(self.devices.values(), key=lambda d: (not d.known, not d.is_gateway, d.ip))

        result = {
            'devices': [asdict(d) for d in device_list],
            'arrivals': [asdict(d) for d in self.arrivals],
            'departures': [asdict(d) for d in self.departures],
            'device_count': len(self.devices),
            'known_count': sum(1 for d in self.devices.values() if d.known),
            'unknown_count': sum(1 for d in self.devices.values() if not d.known),
            'gateway_ip': self.gateway_ip,
            'timestamp': now
        }

        if self.scan_count % 10 == 0:  # Log every 10th scan
            logger.info(f"Network scan #{self.scan_count}: {result['device_count']} devices "
                       f"({result['known_count']} known, {result['unknown_count']} unknown)")

        return result

    async def add_known_device(self, mac: str, name: str):
        """Add a device to the known devices registry"""
        mac = mac.lower()
        self.known_devices[mac] = name

        # Update in Redis
        if self.redis_client:
            try:
                await self.redis_client.set(
                    "sentient:network:known_devices",
                    json.dumps(self.known_devices)
                )
            except Exception as e:
                logger.error(f"Could not save known devices: {e}")

        # Update existing device record if present
        if mac in self.devices:
            self.devices[mac].known = True
            self.devices[mac].name = name

        logger.info(f"Added known device: {mac} = {name}")

    def get_summary(self) -> str:
        """Get a human-readable summary for Cortana to use"""
        if not self.devices:
            return "No devices detected on the network yet."

        known = [d for d in self.devices.values() if d.known]
        unknown = [d for d in self.devices.values() if not d.known and not d.is_gateway]
        gateway = [d for d in self.devices.values() if d.is_gateway]

        parts = []
        parts.append(f"{len(self.devices)} devices on the network")

        if known:
            names = [d.name for d in known if d.name]
            if names:
                parts.append(f"Known: {', '.join(names)}")

        if unknown:
            parts.append(f"{len(unknown)} unknown device{'s' if len(unknown) != 1 else ''}")

        return ". ".join(parts) + "."
