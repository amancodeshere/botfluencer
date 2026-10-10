# Railway rehearsal infrastructure

See [the deployment runbook](../docs/railway.md). This configuration describes
one app and one Postgres database. It is mock-only and has not been applied.

Install infrastructure tooling only when using it:

```sh
npm --prefix .railway ci
npm --prefix .railway run check
```

The Python runtime has no Node dependency. Railway CLI `config plan`/`config apply`
evaluate `.railway/railway.ts`; application deployments do not apply it themselves.
Use a dedicated project because omitted resources can be planned for deletion.
