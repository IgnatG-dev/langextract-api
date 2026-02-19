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

When `WEBHOOK_SECRET` is configured, every webhook POST includes two
extra headers:

| Header                  | Value                                              |
|-------------------------|----------------------------------------------------|
| `X-Webhook-Timestamp`   | Unix epoch seconds (integer as string)             |
| `X-Webhook-Signature`   | Hex-encoded HMAC-SHA256 of `{timestamp}.{body}`   |

### Signature construction

```
message  = f"{timestamp}.".encode() + raw_body_bytes
signature = HMAC-SHA256(secret.encode(), message).hexdigest()
```

### Verification (receiver side)

1. Read `X-Webhook-Timestamp` and `X-Webhook-Signature` headers.
2. Reject if the timestamp is older than 5 minutes (replay
   protection).
3. Compute the expected signature:

   ```python
   expected = hmac.new(
       secret.encode(),
       f"{timestamp}.".encode() + raw_body,
       hashlib.sha256,
   ).hexdigest()
   ```

4. Use `hmac.compare_digest(expected, received_signature)` to
   compare (constant-time).

## API Key Management

API keys (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `LANGEXTRACT_API_KEY`)
are loaded from environment variables or a `.env` file and never
logged or returned in API responses.  Worker processes inherit the
same environment.
