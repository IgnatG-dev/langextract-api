# Security

This document describes the security measures built into the
LangExtract API.

## SSRF Protection

All user-supplied URLs (`document_url`, `callback_url`) pass through
`app.core.security.validate_url()` before the worker fetches or
POSTs to them.  The check enforces:

| Guard                  | Detail                                                 |
|------------------------|-------------------------------------------------------|
| **Scheme allowlist**   | Only `http` and `https` are accepted.                 |
| **Hostname check**     | `localhost` is explicitly blocked.                    |
| **URL length**         | URLs longer than 2 048 characters are rejected.       |
| **DNS resolution**     | Resolves the hostname and rejects private/reserved IPs (RFC 1918, link-local, loopback, IPv6 `::1`). |
| **DNS timeout**        | Resolution times out after 5 s to prevent slow DNS attacks. |
| **Domain allowlist**   | When `ALLOWED_URL_DOMAINS` is set, only listed domains (and their sub-domains) are permitted. |

### DNS Rebinding Caveat

The current implementation resolves the hostname at validation time.
A malicious DNS server could return a public IP during validation
and a private IP when the worker later connects ("DNS rebinding").
For high-security deployments, pin resolved IPs and pass them to the
HTTP client directly (a future enhancement).

## Webhook HMAC Signing

When `WEBHOOK_SECRET` is configured, every webhook POST includes:

- `X-Webhook-Signature` — `HMAC-SHA256(secret, "<timestamp>.<body>")`
- `X-Webhook-Timestamp` — Unix epoch seconds

Consumers should verify the signature and reject timestamps older
than a few minutes to prevent replay attacks.

## API Key Management

API keys (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `LANGEXTRACT_API_KEY`)
are loaded from environment variables or a `.env` file and never
logged or returned in API responses.  Worker processes inherit the
same environment.
