import { ApiStatus } from "@/components/ApiStatus";

/**
 * The workspace views land at M8. Until then this page states plainly which
 * surfaces exist and which do not — an empty-looking "coming soon" screen that
 * implies working features would be exactly the fabricated-completeness the
 * project's reliability rules forbid.
 */
const VIEWS = [
  {
    name: "Research workspace",
    path: "/research",
    summary: "Ask a research question in plain language; watch the AI call tools.",
    milestone: "M8.2",
  },
  {
    name: "Market",
    path: "/market/[symbol]",
    summary: "Instrument selector, price chart, indicators, current signal state.",
    milestone: "M8.3",
  },
  {
    name: "Strategy Lab",
    path: "/strategies",
    summary: "Configure and run backtests, compare strategies side by side.",
    milestone: "M8.4",
  },
  {
    name: "Portfolio & Risk",
    path: "/portfolio",
    summary: "Holdings, allocation, risk metrics, correlation, scenario analysis.",
    milestone: "M8.5",
  },
  {
    name: "Experiments & Journal",
    path: "/experiments",
    summary: "Saved backtests, trade history, reruns, AI-generated analysis.",
    milestone: "M8.6",
  },
] as const;

export default function HomePage() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-16">
      <header className="mb-10">
        <p className="mb-2 text-xs font-semibold tracking-[0.2em] text-accent uppercase">
          Local research terminal
        </p>
        <h1 className="text-3xl font-semibold">AI Quant Terminal</h1>
        <p className="mt-3 max-w-2xl text-text-secondary">
          Market data, indicators, strategies, backtests, portfolio risk and
          reproducible experiments in one place — with an AI researcher that{" "}
          <em>operates</em> the quantitative system rather than inventing
          quantitative results. Every number traces back to a real calculation
          over validated data.
        </p>
      </header>

      <div className="mb-10">
        <ApiStatus />
      </div>

      <section>
        <h2 className="mb-4 text-sm font-semibold tracking-wide text-text-secondary uppercase">
          Workspace views
        </h2>
        <ul className="divide-y divide-border-subtle overflow-hidden rounded-lg border border-border-subtle bg-surface-1">
          {VIEWS.map((view) => (
            <li key={view.path} className="flex items-start gap-4 p-4">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{view.name}</p>
                <p className="mt-0.5 text-sm text-text-secondary">
                  {view.summary}
                </p>
                <code className="tabular mt-1 block text-xs text-text-muted">
                  {view.path}
                </code>
              </div>
              <span className="shrink-0 rounded border border-border-subtle px-2 py-1 text-xs text-text-muted">
                not built yet · {view.milestone}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-text-muted">
          This build is the M1 skeleton: the app shell and its connection to the
          Go API. No view above is implemented yet, and nothing here can place a
          real-money order.
        </p>
      </section>
    </main>
  );
}
