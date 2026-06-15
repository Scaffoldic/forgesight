# Security Policy

We take the security of ForgeSight seriously. Thank you for helping keep the project and
its users safe.

## Supported versions

ForgeSight is pre-1.0 (0.x). Every workspace package shares one version on a coordinated
release. Security fixes land on the **latest released minor**; please upgrade to the most
recent release before reporting.

| Version | Supported          |
|---------|--------------------|
| 0.x (latest minor) | :white_check_mark: |
| older 0.x | :x: |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions,
or pull requests.**

Instead, use **GitHub's private vulnerability reporting**:

1. Go to the repository's **Security** tab → **Report a vulnerability** (Privately report a
   vulnerability).
2. This opens a private security advisory visible only to you and the maintainers.

> If you cannot use private advisories, contact a maintainer privately. _(Maintainer: add a
> security contact email here if you want a non-GitHub channel — otherwise GitHub private
> advisories are the sole channel.)_

### What to include

- A description of the vulnerability and its impact.
- Steps to reproduce (a minimal proof-of-concept is ideal).
- Affected version(s) and environment (Python version, OS, relevant packages).
- Any suggested remediation, if you have one.

### What to expect

- **Acknowledgement within 72 hours.**
- An initial assessment and severity triage shortly after.
- Coordinated disclosure: we'll agree on a timeline, prepare a fix and an advisory, and
  credit you (unless you prefer to remain anonymous).

## Scope & hardening notes

ForgeSight is a telemetry **client**: it observes agents and exports records to backends.
A few areas worth attention when assessing or contributing:

- **Content capture is opt-in (P7).** Prompts, completions, tool arguments/results, and
  eval explanations are **not** captured unless `capture_content` is enabled, and the
  redaction interceptor runs before export. Treat any change that could leak content by
  default as a security issue.
- **Secrets in config.** DSNs, API keys, and tokens are read from env / config and must
  never be logged. Exporters that authenticate (Langfuse, Datadog, OTLP headers) must keep
  credentials out of logs and out of telemetry attributes.
- **Vendor SDKs are isolated.** A vulnerability in a wrapped vendor SDK affects only its
  integration package, not the core — pin and upgrade within that package.

Issues that are purely about a third-party backend or model provider should be reported to
that vendor; we'll help coordinate where ForgeSight is in the path.
