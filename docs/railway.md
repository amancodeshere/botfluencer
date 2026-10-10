# Railway deployment runbook

This repo is ready for a mock rehearsal deployment. No Railway project has been
created or changed. Deploying/provisioning is a separate user-authorized step.
Do not enable live competition mode: it is intentionally unavailable.

## Shape

One application service, one Postgres service, one app replica. The app listens
on Railway's `PORT` and stores all durable state in Postgres. The app needs no
persistent filesystem volume. Keep Postgres storage persistent and configure
database backups before using the rehearsal for anything important.

The Dockerfile installs locked production dependencies and the package including
migrations. It runs as a non-root user. Migrations run in a separate pre-deploy
command, not during the image build or every worker startup.

## Configuration as code

Railway's current documentation directs new services to Infrastructure as Code
instead of legacy `railway.json`/`railway.toml`. This repo uses
`.railway/railway.ts`. The Python app does not need Node; the optional infra
tooling does.

```sh
npm --prefix .railway ci
npm --prefix .railway run check
```

When deployment is authorized, install Railway CLI 5.42.1 or newer, authenticate, and link
this checkout to a **dedicated Botfluencer project/environment**. From the repo
root, preview with `railway config plan`. Review the resource and variable changes
before `railway config apply`. This file owns the environment's whole resource
graph; omitted resources can be deleted. Do not apply it to a shared project.

The config declares the existing GitHub repo on `main`. Commits must be pushed
before Railway can deploy them; this scaffolding task only created local commits.
IaC is evaluated by CLI plan/apply, not automatically by every GitHub deploy.
After applying, inspect the actual build/deploy settings in Railway. The config
explicitly selects the root Dockerfile as the build source.

## Required settings

| Setting | Value |
| --- | --- |
| Database URL | Reference to the Postgres service's `DATABASE_URL` |
| Build | Root Dockerfile |
| Pre-deploy command | `botfluencer db upgrade` |
| Pre-deploy timeout | 120 seconds |
| Start command | `botfluencer serve` |
| Healthcheck path | `/health/ready` |
| Healthcheck timeout | 120 seconds |
| Replicas | 1 |
| Restart policy | On failure, up to 10 retries |
| Serverless/sleep | Disabled |
| Drain time | 20 seconds |
| Mode | `BOT_MODE=replay` |
| Namespace | Stable namespace derived from the environment ID |

Use the direct Postgres connection, not transaction-mode PgBouncer: worker
ownership uses a session advisory lock on a dedicated physical connection.
Keep the database private; use the public connection only for an explicitly
needed local operation. Never copy a production URL into commands or Git.

## Readiness and handover

`/health/ready` verifies initialized storage and either active/fresh worker state
or standby state. A standby process takes no actions. Railway can pass readiness,
switch deployments, and stop the old process; the replacement acquires ownership
on its next poll. Requiring ownership for deployment readiness would prevent the
old process from being stopped during normal deployment overlap.

`/health/worker` requires ownership, a recent completed tick, and database access.
Configure ongoing uptime monitoring against this endpoint with a handover grace
period. Railway only uses its deployment healthcheck during startup, not as an
ongoing watchdog. Fatal worker errors exit the process to trigger restart policy.
After retries are exhausted, inspect the deployment/logs and fix the underlying
configuration, schema, database, or adapter issue before restarting.

## Rehearsal acceptance

1. Confirm pre-deploy migrations succeed and `/health/worker` becomes healthy.
2. Check `botfluencer status` through an authenticated Railway shell/SSH session.
3. Pause, restart, and confirm the pause persists and heartbeat continues.
4. Resume; confirm fixture actions complete once and further restarts don't repeat
   them. Redeploy once to verify standby handover in the actual Railway environment.
5. Run for 24 hours while recording heartbeat availability and restart events.
6. Enable backups and restore a backup into a separate rehearsal database.

The local tests and container smoke checks are shorter checks. They do not mean
a 24-hour Railway rehearsal, hosted monitoring, or backup restore has been done.

## Sources checked for this scaffold

- [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code)
- [IaC reference](https://docs.railway.com/infrastructure-as-code/reference)
- [Pre-deploy commands](https://docs.railway.com/deployments/pre-deploy-command)
- [Deployment healthchecks](https://docs.railway.com/deployments/healthchecks)

Recheck provider documentation when changing deployment behavior.
