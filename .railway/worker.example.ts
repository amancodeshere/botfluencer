import { defineRailway, github, postgres, project, service } from "railway/iac";

// Unapplied example only. Do not apply over the existing template database.
// Import/reconcile existing resources first. Applying provisions paid resources.
// Use a dedicated Railway project; this file owns the whole environment graph.
export default defineRailway((ctx) => {
  const db = postgres("Postgres");
  const app = service("botfluencer", {
    source: github("amancodeshere/botfluencer", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "botfluencer serve",
    preDeploy: "botfluencer db upgrade",
    healthcheck: "/health/ready",
    healthcheckTimeout: 120,
    replicas: 1,
    env: {
      DATABASE_URL: db.env.DATABASE_URL,
      BOT_NAMESPACE: `railway-${ctx.environmentId}-replay`,
      BOT_MODE: "replay",
      BOT_STRATEGY: "noop",
      BOT_PLATFORM_BACKEND: "mock",
      CTN_NETWORK_ENABLED: "false",
      BOT_ACCOUNTS: '["mock-01"]',
      BOT_POLL_INTERVAL_SECONDS: "5",
      BOT_OPERATION_TIMEOUT_SECONDS: "30",
      BOT_HEARTBEAT_TIMEOUT_SECONDS: "60",
      BOT_MAX_ACTIONS_PER_HOUR: "60",
      BOT_ACCOUNT_COOLDOWN_SECONDS: "5",
      BOT_MAX_LLM_CALLS_PER_HOUR: "60",
      BOT_MAX_ACTION_ATTEMPTS: "3",
      BOT_RETRY_BASE_SECONDS: "2",
      BOT_FIXTURE_PATH: "/app/fixtures/feed.jsonl",
    },
    deploy: {
      preDeployTimeoutSeconds: 120,
      drainingSeconds: 20,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      sleepApplication: false,
    },
  });
  return project("botfluencer", { resources: [db, app] });
});
