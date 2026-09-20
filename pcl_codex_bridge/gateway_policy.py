"""Validate gateway exposure before installation or socket creation."""
import ipaddress

LOOPBACK_CIDRS = "127.0.0.0/8,::1/128"


def management_networks(host, cidrs):
    networks = tuple(
        ipaddress.ip_network(value.strip(), strict=False)
        for value in cidrs.split(",") if value.strip()
    )
    if not networks:
        raise ValueError("At least one gateway admin CIDR is required")
    try:
        local_only = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local_only = host.lower() == "localhost"
    if not local_only and all(network.is_loopback for network in networks):
        raise ValueError(
            "Non-loopback gateway requires explicit remote management CIDRs; "
            "set --admin-cidrs or PCL_RELAY_ADMIN_CIDRS. "
            "Otherwise model health can pass while portal CONNECT is denied."
        )
    return networks
