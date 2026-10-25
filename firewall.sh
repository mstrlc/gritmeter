#!/bin/sh
# Keep the gritmeter containers (bridge br-gritmeter, see compose.yaml) off the Pi, the LAN and the tailnet: they may
# talk to each other and to the internet (Strava, Overpass, Cloudflare, DNS), nothing else. Run as root on the Pi;
# idempotent. gritmeter-firewall.service runs it at boot before Docker. The rules match the bridge by name, so they
# hold even before the network exists.
set -eu
BR=br-gritmeter
CHAIN=GRITMETER

iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"
iptables -A "$CHAIN" -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
iptables -A "$CHAIN" -o "$BR" -j RETURN                       # gritmeter <-> cloudflared
for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4; do
  iptables -A "$CHAIN" -d "$net" -j DROP                      # private, tailnet, link-local, multicast
done

# Forwarded traffic (LAN, tailnet, other containers): DOCKER-USER runs before Docker's own rules.
iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER -i "$BR" -j "$CHAIN" 2>/dev/null || iptables -I DOCKER-USER 1 -i "$BR" -j "$CHAIN"
iptables -C FORWARD -j DOCKER-USER 2>/dev/null || iptables -I FORWARD 1 -j DOCKER-USER

# Traffic to the Pi itself (any of its addresses, e.g. Home Assistant, VNC): nothing on the host is needed.
iptables -C INPUT -i "$BR" -m conntrack --ctstate NEW -j DROP 2>/dev/null \
  || iptables -I INPUT 1 -i "$BR" -m conntrack --ctstate NEW -j DROP
