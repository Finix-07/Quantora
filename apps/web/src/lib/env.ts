/**
 * Client-visible configuration.
 *
 * The UI consumes the Go API and nothing else — never PostgreSQL, never the
 * Python engine directly (architecture.md §3.4). The base URL is injected at
 * build/run time so the same image works whether the API is reached from the
 * browser (localhost:8080) or from inside the Compose network (api:8080).
 */

const DEFAULT_API_BASE_URL = "http://localhost:8080";

/** Base URL of the Go API, without a trailing slash. */
export const API_BASE_URL: string = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

/**
 * True when the URL came from configuration rather than the built-in default.
 * The shell surfaces this so a misconfigured stack looks misconfigured instead
 * of looking like a broken API.
 */
export const API_BASE_URL_IS_EXPLICIT: boolean =
  typeof process.env.NEXT_PUBLIC_API_BASE_URL === "string" &&
  process.env.NEXT_PUBLIC_API_BASE_URL.length > 0;
