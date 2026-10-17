# Railway runbook

## Existing resources

The dedicated [botfluencer project](https://railway.com/project/05c6e05a-bbd5-4085-ab24-e21aa3758bd7)
has one `Postgres` service in `production`, running Postgres 18. A password-protected
public TCP endpoint is enabled for native development and isolated tests. Clients
must use `sslmode=require`. Connection URLs are private Railway service variables.

| Variable on Postgres | Purpose |
| --- | --- |
| `BOTFLUENCER_DATABASE_URL` | Restricted application role, private network |
| `BOTFLUENCER_DATABASE_PUBLIC_URL` | Same application database, public TLS |
| `TEST_DATABASE_PUBLIC_URL` | Separate test database and restricted test role |

The template's original admin credentials remain for database administration.
Application and tests use their own roles. Do not expose variables in logs or
screenshots. `scripts/railway_env.py` captures only the selected URL into an ignored
private file. Rotate credentials through an agreed maintenance procedure and
refresh local files afterward.

No worker service has been provisioned or deployed. No Docker database is used.
The root Dockerfile packages only the Python application for a future deployment.

## Future worker deployment

Creating/deploying the worker is a separate authorized stage. The example
`.railway/worker.example.ts` is a proposed whole environment graph, not an
import of the existing template. Do not apply it to this project: replacing the
resource graph could replace or delete the existing database. Import and reconcile
existing resources and review the complete CLI plan before any future IaC apply.
Type checking the example does not establish deployment safety.

Configure the future worker with:

| Setting | Value |
| --- | --- |
| Build | Root Dockerfile |
| Pre-deploy | `botfluencer db upgrade`, timeout 120 seconds |
| Start | `botfluencer serve` |
| Database | Reference `${{Postgres.BOTFLUENCER_DATABASE_URL}}` |
| Healthcheck | `/health/ready`, timeout 120 seconds |
| Replicas | 1 |
| Restart | On failure, maximum 10 retries |
| Sleep | Disabled |
| Drain | 20 seconds |
| Initial backend / mode / strategy | `mock` / `replay` / `noop` |
| Namespace | Stable, unique to environment and workload |

Use Railway secret/reference variables for team credentials. Set
`CTN_NETWORK_ENABLED=false` initially. Competition observation later requires a
separate namespace, backend `ctn`, mode `observe`, explicit network opt-in, and the
opening gate. Live publishing is blocked in code.

Deployment readiness permits standby during overlap. Monitor `/health/worker`
with handover grace for ongoing health; Railway's deployment check is not an
ongoing watchdog. Inspect logs after exhausted restart retries.

Before competition use, verify hosted handover, pause/restart persistence, a 24-hour
soak, alerting, backup scheduling, and restore into a separate database. These
hosted operational checks remain pending.
