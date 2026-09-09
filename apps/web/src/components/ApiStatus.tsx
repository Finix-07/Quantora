"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT } from "@/lib/env";

type Dependency = { status: string; error?: string; detail?: string };
type Health = {
  status: string;
  service: string;
  version: string;
  dependencies: Record<string, Dependency>;
};

type State =
  | { kind: "loading" }
  | { kind: "reachable"; health: Health }
  | { kind: "unreachable"; reason: string };

/**
 * Live connection status for the Go API.
 *
 * The point is diagnostic honesty: if the API is down or a dependency is
 * unhealthy, the user should see which one and where the UI was pointed, rather
 * than an empty page that looks like the product has no data.
 */
export function ApiStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);

    fetch(`${API_BASE_URL}/healthz`, { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        // 503 still carries a well-formed body naming the failing dependency,
        // so it is parsed rather than treated as an outage.
        const health = (await response.json()) as Health;
        setState({ kind: "reachable", health });
      })
      .catch((error: unknown) => {
        const reason =
          error instanceof DOMException && error.name === "AbortError"
            ? "the request timed out after 5s"
            : error instanceof Error
              ? error.message
              : "unknown error";
        setState({ kind: "unreachable", reason });
      })
      .finally(() => clearTimeout(timeout));

    return () => {
      clearTimeout(timeout);
      controller.abort();
    };
  }, []);

  return (
    <section
      className="rounded-lg border border-border-subtle bg-surface-1 p-5"
      aria-live="polite"
      data-testid="api-status"
    >
      <header className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold tracking-wide text-text-secondary uppercase">
          Backend connection
        </h2>
        <code className="tabular text-xs text-text-muted">{API_BASE_URL}</code>
      </header>

      {!API_BASE_URL_IS_EXPLICIT && (
        <p className="mb-3 text-xs text-warning">
          NEXT_PUBLIC_API_BASE_URL is not set; falling back to the built-in
          default. Set it in <code>.env</code> if the API runs elsewhere.
        </p>
      )}

      {state.kind === "loading" && (
        <p className="text-sm text-text-secondary">Checking…</p>
      )}

      {state.kind === "unreachable" && (
        <div className="text-sm">
          <p className="font-medium text-negative">API unreachable</p>
          <p className="mt-1 text-text-secondary">
            {state.reason}. Start the stack with{" "}
            <code className="tabular">docker compose up</code>, then reload.
          </p>
        </div>
      )}

      {state.kind === "reachable" && (
        <div className="text-sm">
          <p
            className={
              state.health.status === "ok"
                ? "font-medium text-positive"
                : "font-medium text-warning"
            }
          >
            {state.health.status === "ok" ? "Connected" : "Connected — degraded"}
            <span className="ml-2 text-text-muted">
              ({state.health.service} {state.health.version})
            </span>
          </p>
          <ul className="mt-2 space-y-1">
            {Object.entries(state.health.dependencies ?? {}).map(([name, dep]) => (
              <li key={name} className="flex items-baseline gap-2 text-xs">
                <span
                  className={
                    dep.status === "ok" ? "text-positive" : "text-negative"
                  }
                  aria-hidden
                >
                  ●
                </span>
                <span className="tabular text-text-secondary">{name}</span>
                <span className="text-text-muted">
                  {dep.error ?? dep.detail ?? dep.status}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
