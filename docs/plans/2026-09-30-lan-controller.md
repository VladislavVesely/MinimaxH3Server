# Explicit trusted-LAN controller mode

Owner requested direct `http://<Mac-IP>:port` access without an SSH tunnel and accepted trusted-LAN-only HTTP.

- Keep loopback as default. `--lan-ip` binds only one explicitly supplied RFC1918 IPv4 address; no wildcard, public IP or arbitrary hostname.
- Preserve exact Host/Origin, cookies, CSRF, bounded requests and SSH host-key verification. Disable forwarded-header interpretation; reject non-LAN/non-loopback peers.
- LAN mode generates a cryptographically random access key per process, saved in an owner-only file in the existing private state directory. Bare-address browsers see an access-key login. Successful key login authorizes the existing shared controller session; no separate user/workspace isolation is promised. Key grants full controller access. Limit login attempts; restart invalidates keys/sessions.
- Original one-use fragment bootstrap remains available and unchanged in default loopback mode. LAN key login is enabled only for explicit LAN mode.
- Use `crypto.getRandomValues` for opaque idempotency IDs because `randomUUID` is unavailable on insecure HTTP origins.
- No router/firewall automation, TLS claims, remote daemon changes or termination of existing controller/joiner sessions.

Acceptance: security regressions, real browser login over the Mac's private-IP HTTP listener in Chrome/WebKit, default-loopback tests, owner-only key-file permissions. Physical LAN reachability was also confirmed from the Ubuntu machine: the access-mode endpoint returned 200 and unauthenticated controller status returned 401. Other devices on isolated guest Wi-Fi may still need network/firewall configuration.
