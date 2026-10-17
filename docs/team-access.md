# Team configuration and access

Repository: `amancodeshere/botfluencer`. It is public. Keep credentials and private
team settings in encrypted repository secrets; configuration variables are not
secret and must never contain passwords, tokens, or database URLs.

## GitHub Actions inventory

The requested secret inventory below is pending explicit export approval. Verify
names with `gh secret list --repo amancodeshere/botfluencer`; never print values.

| Repository secret | Purpose |
| --- | --- |
| `TEST_DATABASE_URL` | Isolated Railway test database; consumed by integration CI |
| `DATABASE_URL` | Restricted application database via public TLS; future authorized workflows |
| `CTN_TEAM_CODE` | Competition authentication/model credential; future authorized workflows |
| `CTN_SIDE` | Assigned side, kept private with team settings |
| `CTN_MAX_TOTAL_ACCOUNTS` | Portal-verified total allowance |
| `CTN_MAX_ACTIVE_ACCOUNTS` | Portal-verified active allowance |

No database admin password or personal Railway token is shared with GitHub.
`DATABASE_URL` names the application database; it must never be substituted for
`TEST_DATABASE_URL`. Rotation requires updating Railway, local private files, and
the corresponding GitHub secret. Use stdin / an interactive secret prompt, never
a literal credential in a command argument, issue, log, or committed file.

Non-sensitive repository variables contain:

- `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`, `RAILWAY_ENVIRONMENT_NAME`,
  `RAILWAY_SERVICE_NAME`: identify the existing database environment.
- `BOT_MODE=replay`, `BOT_PLATFORM_BACKEND=mock`, `BOT_STRATEGY=noop`,
  `CTN_NETWORK_ENABLED=false`: safe baseline configuration.
- `CTN_FEED=trending`, `CTN_FEED_LIMIT=20`, `CTN_LLM_MAX_TOKENS=150`:
  baseline request preferences.

Repository variables are not automatically injected into a process. A workflow
must explicitly map `${{ vars.NAME }}` into its environment. Likewise, secrets
must be explicitly mapped with `${{ secrets.NAME }}`. Current integration CI
receives only TEST_DATABASE_URL; tests have no need for the application credential
or team code. Adding credentials does not enable competition access or deployment.

## Local teammate setup

GitHub Actions secrets are write-only through the UI/API: collaborators cannot
fetch their values into a local `.env`. Listing secrets returns names/metadata.
Do not build an Action that prints credentials or uploads an environment artifact.

For native development, each teammate needs access to the existing Railway
project and the CTN team portal. No invitations were sent by this setup task.

1. Authenticate Railway CLI with your own identity and link the existing project.
2. Run the README's `scripts/railway_env.py` commands to create private `.env`
   and `.env.test`. They use restricted credentials and require TLS.
3. Copy the team code, assigned side, and account allowances from your authenticated
   CTN portal into `.env` privately. Leave CTN_NETWORK_ENABLED=false.
4. Use a personal workload namespace to avoid competing with another developer's
   worker ownership lock. Use the test database only for disposable tests.

GitHub repository access does not automatically grant Railway or CTN access.
Codespaces, Dependabot, and GitHub agent secret stores are separate from Actions
and have not been configured. No personal access token is needed in repository
secrets for ordinary CI; GitHub supplies a scoped GITHUB_TOKEN.

## Workflow access

Collaborators who can change workflows may use repository secrets, so review
workflow edits like credential access changes. Fork pull requests and Dependabot
runs do not receive normal Actions secrets; the integration job's required-secret
check fails clearly rather than passing without persistence tests. Never switch
to pull_request_target to run untrusted pull-request code with these credentials.

References: [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets),
[Actions secrets REST API](https://docs.github.com/en/rest/actions/secrets).
