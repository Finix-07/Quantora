/**
 * Liveness endpoint for the `web` container's Compose healthcheck.
 *
 * It reports only that the Next.js server is serving. It deliberately does not
 * probe the Go API: the web tier being up and the API being down are different
 * failures, and collapsing them would make `docker compose ps` misleading.
 */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({
    status: "ok",
    service: "web",
    checked_at: new Date().toISOString(),
  });
}
