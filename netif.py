"""Consulta de interfaces de red (adaptadores) del sistema mediante QtNetwork."""
from __future__ import annotations

from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

VPN_HINTS = ("vpn", "tap-", "tap ", "tun", "wireguard", "wintun", "nordlynx", "openvpn",
             "proton", "mullvad", "surfshark", "expressvpn", "windscribe", "cisco anyconnect",
             "fortinet", "zerotier", "tailscale", "hamachi", "pia ", "private internet access")


def is_vpn_name(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in VPN_HINTS)


def list_interfaces() -> list[dict]:
    """Adaptadores conectados (sin loopback): [{name, ips, vpn}].
    `ips` incluye IPv4 e IPv6 globales; se omiten las IPv6 de enlace local (fe80::)."""
    out = []
    for iface in QNetworkInterface.allInterfaces():
        flags = iface.flags()
        if not (flags & QNetworkInterface.InterfaceFlag.IsUp) or not (flags & QNetworkInterface.InterfaceFlag.IsRunning):
            continue
        if flags & QNetworkInterface.InterfaceFlag.IsLoopBack:
            continue
        ips = []
        for entry in iface.addressEntries():
            ip = entry.ip()
            if ip.isLoopback() or ip.isLinkLocal():
                continue
            if ip.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                ips.insert(0, ip.toString())
            elif ip.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv6Protocol:
                ips.append(ip.toString().split("%")[0])
        if not ips:
            continue
        name = iface.humanReadableName()
        desc = name
        try:
            desc = f"{name} {iface.name()}"
        except Exception:
            pass
        out.append({"name": name, "ips": ips, "vpn": is_vpn_name(desc)})
    return out


def addresses_of(name: str) -> list[str]:
    for iface in list_interfaces():
        if iface["name"] == name:
            return iface["ips"]
    return []
