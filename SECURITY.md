# Security Policy

## Supported versions

Security fixes are applied to the current `main` branch and the latest release.

## Reporting a vulnerability

Please do not open a public issue for credential exposure, unsafe defaults, dependency vulnerabilities or other security-sensitive reports. Use GitHub's private vulnerability reporting for this repository, or contact the repository owner privately with a minimal reproduction and impact summary.

Never include API keys, session cookies, browser profiles or private page data in a report.

## Current Network Boundary

The approval-gated HTTP and browser tools reject non-public addresses, unapproved hosts, and ports other than 80/443. Browser extraction also rejects arbitrary CDP, launch, context, proxy, storage, action and model configuration; it permits only GET/HEAD/OPTIONS, blocks WebSockets and service workers, disables QUIC and non-proxied WebRTC UDP, and applies request-count and execution-time limits. Any blocked browser request fails the step instead of silently returning partial data.

Real Chromium integration tests run against an ephemeral local fixture server
on Ubuntu and Windows. They verify allowed same-host navigation/subresources and
blocking for unapproved/private hosts, cross-host redirects, POST, WebSocket,
service worker activation, request limits, and total duration. A blocked run
does not emit a partial crawler artifact. The fixture uses a test-only loopback
validator; production public-address validation is not relaxed.

This is application-layer defense in depth, not a complete network sandbox. DNS
validation and the eventual httpx or Chromium connection do not yet share a
pinned resolver or controlled egress proxy, so a DNS validation/connect race
remains. The browser policy also does not yet enforce one aggregate byte budget
across every response body. Do not treat Assistant browser extraction as strong
isolation on a host that can reach sensitive private networks; its status stays
**Limited**. Connection pinning and aggregate browser-byte metering are tracked
as follow-up hardening in
[#14](https://github.com/Ulysses-G-Yang/approval-first-research-automation/issues/14).

The standalone `GenericSpider` keeps its legacy trusted-configuration network
behavior. JavaScript `network_json` capture is passive and bounded per capture;
it is not an egress sandbox and must be used only for owned or explicitly
authorized targets. The project does not implement CAPTCHA, access-control, or
risk-control evasion.
