# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch and the latest release.

## Reporting a vulnerability

Please do not open a public issue for credential exposure, unsafe defaults, dependency vulnerabilities or other security-sensitive reports. Use GitHub's private vulnerability reporting for this repository, or contact the repository owner privately with a minimal reproduction and impact summary.

Never include API keys, session cookies, browser profiles or private page data in a report.

## Current Network Boundary

The approval-gated HTTP and browser tools reject non-public addresses, unapproved hosts, and ports other than 80/443. Browser extraction also rejects arbitrary CDP, launch, context, proxy, storage, action and model configuration; it permits only GET/HEAD/OPTIONS, blocks WebSockets and service workers, disables QUIC and non-proxied WebRTC UDP, and applies request-count and execution-time limits. Any blocked browser request fails the step instead of silently returning partial data.

This is application-layer defense in depth, not a complete network sandbox. DNS validation and the eventual httpx or Chromium connection do not yet share a pinned resolver or controlled egress proxy, so DNS rebinding remains a known limitation. Do not treat browser extraction as strong isolation when it runs on a host with access to sensitive private networks.
