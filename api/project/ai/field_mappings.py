"""Cross-service field translation mappings.

These mappings translate between ECS (Elastic Common Schema) field names used
throughout Malcolm and tool-native field names used by individual backends.
"""

import urllib.parse

# Comprehensive ECS -> Arkime field mapping
ECS_TO_ARKIME = {
    # IP addresses
    "source.ip": "ip.src",
    "destination.ip": "ip.dst",
    "related.ip": "ip",
    "client.ip": "ip.src",
    "server.ip": "ip.dst",
    # Ports
    "source.port": "port.src",
    "destination.port": "port.dst",
    "client.port": "port.src",
    "server.port": "port.dst",
    # Network
    "network.protocol": "protocols",
    "network.transport": "ipProtocol",
    "network.vlan.id": "vlan",
    "network.bytes": "bytes",
    "network.community_id": "communityId",
    # MAC addresses
    "source.mac": "mac.src",
    "destination.mac": "mac.dst",
    # GeoIP
    "source.geo.country_iso_code": "geoip.src",
    "destination.geo.country_iso_code": "geoip.dst",
    # DNS
    "zeek.dns.query": "host.dns",
    "dns.question.name": "host.dns",
    # HTTP
    "zeek.http.uri": "http.uri",
    "zeek.http.method": "http.method",
    "http.request.method": "http.method",
    "url.full": "http.uri",
    "user_agent.original": "http.useragent",
    # TLS/SSL
    "tls.server.ja3s": "tls.ja3s",
    "tls.client.ja3": "tls.ja3",
    # File
    "file.hash.md5": "file.md5",
    "file.hash.sha256": "file.sha256",
    "file.mime_type": "file.mime",
    # Suricata
    "suricata.alert.signature": "suricata.signature",
    "suricata.alert.signature_id": "suricata.signatureId",
    "suricata.alert.severity": "suricata.severity",
    # Tags
    "tags": "tags",
    # Event
    "event.dataset": "node",
    # Zeek notices and detections (pass-through)
    "zeek.notice.category": "zeek.notice.category",
    "zeek.notice.sub_category": "zeek.notice.sub_category",
    "zeek.notice.note": "zeek.notice.note",
    "zeek.weird.name": "zeek.weird.name",
    # Vulnerability / rule fields
    "vulnerability.id": "vulnerability.id",
    "vulnerability.category": "vulnerability.category",
    "rule.category": "rule.category",
    "rule.name": "rule.name",
    # Threat intelligence
    "threat.technique.id": "threat.technique.id",
    "threat.tactic.name": "threat.tactic.name",
}


ARKIME_TO_ECS = {v: k for k, v in ECS_TO_ARKIME.items()}


def _quote_arkime_value(value_str):
    """Quote an Arkime value if it contains special characters."""
    if any(c in value_str for c in ':=/+ ') and not value_str.startswith('"'):
        return f'"{value_str}"'
    return value_str


def ecs_filters_to_arkime_expression(filters: dict) -> str:
    """Translate ECS field:value filters to an Arkime expression string.

    Handles list values (from merge_filters) as OR expressions,
    and special character quoting.
    """
    parts = []
    for ecs_field, value in filters.items():
        arkime_field = ECS_TO_ARKIME.get(ecs_field, ecs_field)
        if isinstance(value, list):
            or_parts = [f'{arkime_field} == {_quote_arkime_value(str(v))}' for v in value]
            parts.append(f'({" || ".join(or_parts)})')
        else:
            parts.append(f'{arkime_field} == {_quote_arkime_value(str(value))}')
    return " && ".join(parts) if parts else "*"


def ecs_filters_to_lucene_query(filters: dict) -> str:
    """Translate ECS field:value filters to a Lucene/DQL query string.

    Handles list values as OR groups and quotes values with special characters.
    """
    parts = []
    for field, value in filters.items():
        if isinstance(value, list):
            or_parts = [f'{field}:"{v}"' for v in value]
            parts.append(f'({" OR ".join(or_parts)})')
        else:
            value_str = str(value)
            if any(c in value_str for c in ' :=/+') and not value_str.startswith('"'):
                parts.append(f'{field}:"{value_str}"')
            else:
                parts.append(f'{field}:{value_str}')
    return " AND ".join(parts) if parts else "*"


def build_arkime_url(expression: str, date: int = -1) -> str:
    """Build a relative URL for the Arkime sessions view."""
    return f"/arkime/sessions?expression={urllib.parse.quote(expression)}&date={date}"


def build_arkime_pcap_url(expression: str, date: int = -1) -> str:
    """Build a relative URL for Arkime PCAP export."""
    return f"/arkime/api/sessions/pcap?expression={urllib.parse.quote(expression)}&date={date}"


def build_netbox_url(query: str) -> str:
    """Build a relative URL for NetBox search."""
    return f"/netbox/search/?q={urllib.parse.quote(query)}"
