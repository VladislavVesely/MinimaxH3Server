"""Explicit RFC1918 IPv4 LAN mode; no wildcard, public-IP or hostname binding."""
import ipaddress

NETWORKS=tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def private_ipv4(value):
    try:
        address=ipaddress.IPv4Address(value)
        return str(address)==value and any(address in network for network in NETWORKS)
    except (ValueError,TypeError):return False


def lan_peer(value):
    try:return private_ipv4(value) or ipaddress.ip_address(value).is_loopback
    except (ValueError,TypeError):return False
