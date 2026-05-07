"""Artifact extraction from user queries via deterministic regex.

Extracts grounding facts -- IPs, time ranges, community IDs, file hashes,
protocol keywords -- from raw query text. These artifacts enrich the LLM
prompt so the model has concrete values to work with.

This module does NOT make routing decisions. That is the LLM's job.
"""

import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ArtifactExtraction(BaseModel):
    ips: list[str] = Field(default_factory=list)
    ports: list[int] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    suricata_sids: list[int] = Field(default_factory=list)
    time_from: Optional[str] = None
    time_to: Optional[str] = None
    community_id: Optional[str] = None
    file_hash: Optional[str] = None
    protocol_hints: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return (not self.ips and not self.ports and not self.domains
                and not self.suricata_sids and not self.time_from
                and not self.community_id and not self.file_hash
                and not self.protocol_hints)


IPV4_PATTERN = re.compile(
    r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?)\b'
)
COMMUNITY_ID_PATTERN = re.compile(
    r'\bcommunity.?id\s+(\S+)'
    r'|'
    r'\b(1:[A-Za-z0-9+/=]{10,28})\b',
    re.IGNORECASE,
)
FILE_HASH_PATTERN = re.compile(
    r'\b([0-9a-fA-F]{32})\b'
    r'|'
    r'\b([0-9a-fA-F]{40})\b'
    r'|'
    r'\b([0-9a-fA-F]{64})\b',
)
TIME_PHRASES = [
    re.compile(r'\blast\s+(\d+)\s+(minute|hour|day|week|month|year)s?\b', re.IGNORECASE),
    re.compile(r'\bpast\s+(\d+)\s+(minute|hour|day|week|month|year)s?\b', re.IGNORECASE),
    re.compile(r'\bsince\s+(yesterday|last\s+week|last\s+month)\b', re.IGNORECASE),
    re.compile(r'\b(\d+)\s+(minute|hour|day|week|month|year)s?\b', re.IGNORECASE),
]

PORT_PATTERN = re.compile(r'\bport\s+(\d{1,5})\b', re.IGNORECASE)
DOMAIN_PATTERN = re.compile(
    r'\b((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})\b'
)
SURICATA_SID_PATTERN = re.compile(r'\bsid[:\s]+(\d{4,})\b|\b(\d{7,})\b', re.IGNORECASE)

# Domains to exclude (common words that look like domains)
_DOMAIN_EXCLUDE = frozenset({
    "e.g.", "i.e.", "etc.", "vs.",
})

PROTOCOL_KEYWORDS = frozenset({
    "dns", "http", "https", "ssh", "ftp", "smtp", "dhcp", "snmp", "ntp",
    "rdp", "smb", "ldap", "kerberos", "irc", "sip", "mqtt", "modbus",
    "ssl", "tls", "quic", "syslog", "mysql", "postgresql", "redis",
    "telnet", "tftp", "ntlm", "radius", "stun", "websocket",
    "bacnet", "dnp3", "profinet", "opcua", "s7comm", "ethercat",
    "enip", "ethernet/ip", "hart", "genisys", "bsap",
})


def is_valid_ipv4(ip_str):
    parts = ip_str.split("/")[0].split(".")
    try:
        return len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def extract(query):
    """Extract artifacts from a user query. Returns an ArtifactExtraction."""
    query_lower = query.lower().strip()

    ips = [ip for ip in IPV4_PATTERN.findall(query) if is_valid_ipv4(ip)]
    ports = _extract_ports(query)
    domains = _extract_domains(query, ips)
    suricata_sids = _extract_suricata_sids(query)
    time_from, time_to = _extract_time(query_lower)
    community_id = _extract_community_id(query)
    file_hash = _extract_file_hash(query)
    protocol_hints = sorted(set(re.findall(r'[a-z0-9/]+', query_lower)) & PROTOCOL_KEYWORDS)

    extraction = ArtifactExtraction(
        ips=ips,
        ports=ports,
        domains=domains,
        suricata_sids=suricata_sids,
        time_from=time_from,
        time_to=time_to,
        community_id=community_id,
        file_hash=file_hash,
        protocol_hints=protocol_hints,
    )

    logger.debug(
        "extracted ips=%s ports=%s domains=%s sids=%s time=%s/%s community_id=%s hash=%s protocols=%s",
        ips, ports, domains, suricata_sids, time_from, time_to, community_id, file_hash, protocol_hints,
    )
    return extraction


def _extract_community_id(query):
    m = COMMUNITY_ID_PATTERN.search(query)
    if m:
        return (m.group(1) or m.group(2)).strip()
    return None


def _extract_file_hash(query):
    m = FILE_HASH_PATTERN.search(query)
    if m:
        return (m.group(1) or m.group(2) or m.group(3)).strip()
    return None


def _extract_time(query_lower):
    for pattern in TIME_PHRASES:
        match = pattern.search(query_lower)
        if match:
            return _time_phrase_to_relative(match), "now"
    return None, None


def _extract_ports(query):
    ports = []
    for m in PORT_PATTERN.finditer(query):
        p = int(m.group(1))
        if 1 <= p <= 65535:
            ports.append(p)
    return sorted(set(ports))


def _extract_domains(query, ips):
    """Extract domain names, excluding IPs and common false positives."""
    candidates = DOMAIN_PATTERN.findall(query)
    domains = []
    ip_set = set(ips)
    for d in candidates:
        if d in _DOMAIN_EXCLUDE or d in ip_set:
            continue
        # Skip if it looks like a version number (e.g. "3.2.1")
        if all(part.isdigit() for part in d.split(".")):
            continue
        domains.append(d.lower())
    return sorted(set(domains))


def _extract_suricata_sids(query):
    matches = SURICATA_SID_PATTERN.findall(query)
    sids = []
    for groups in matches:
        for g in groups:
            if g:
                sids.append(int(g))
    return sorted(set(sids))


def _time_phrase_to_relative(match):
    text = match.group(0).lower()
    if "yesterday" in text:
        return "now-1d"
    if "last week" in text:
        return "now-1w"
    if "last month" in text:
        return "now-1M"
    groups = match.groups()
    if len(groups) >= 2:
        num = groups[0]
        unit = groups[1].lower()
        unit_map = {
            "minute": "m", "hour": "h", "day": "d",
            "week": "w", "month": "M", "year": "y",
        }
        suffix = unit_map.get(unit, "d")
        return f"now-{num}{suffix}"
    return "now-1d"
