# Security Policy

## Supported versions

Only the latest release receives security fixes while the project is in 0.x.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Include what you found, how to reproduce it, and the impact you expect. You will get an acknowledgement within a few days, and a fix or mitigation plan as soon as it is assessed.

## Threat model and hardening notes

This tool runs AI agents that can read and, if you grant them tools, modify files and run commands in a workspace. Treat it like a CI runner.

- **Least privilege.** Agents only get the built-in tools listed in their config. Keep `Write`, `Edit` and `Bash` for the few roles that need them. The default `feature-planning` workflow is read-only.
- **Approval gates.** Use `require_approval: true` on any workflow that writes. Review the plan before calling `/runs/{id}/approve`.
- **Isolation.** Run against a throwaway checkout or inside the provided Docker container, and mount only the project you want changed. Prompt injection from files the agents read is possible; do not give write or shell tools to agents that process untrusted content.
- **Network exposure.** The API binds to `127.0.0.1` by default and Docker publishes the port on localhost only. If you expose it, set `ORCHESTRA_API_TOKEN` (bearer auth, constant-time comparison), terminate TLS in a reverse proxy, and restrict access at the network level. The API has no per-user accounts.
- **Secrets.** `ANTHROPIC_API_KEY` is read from the environment or `.env`; `.env` is git-ignored. Never commit it. Tool inputs recorded in run events are truncated, but may still contain file contents or paths: protect the SQLite database accordingly.
- **Budgets.** Use `ORCHESTRA_MAX_TURNS` and `ORCHESTRA_MAX_BUDGET_USD` to bound runaway runs.
