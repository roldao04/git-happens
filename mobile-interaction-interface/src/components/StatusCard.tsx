import { useState } from "react";
import { useHome } from "../state/HomeProvider";
import { hhmm, learningLine, shownChanges } from "../words";
import { ChevronIcon } from "./icons";
import { PlanList } from "./PlanList";

/** The card under the header: what the home is doing right now; opened, what Alfred has planned. To look at, not to control. */
export function StatusCard({ onEvent }: { onEvent?: (text: string) => void }) {
  const { status, home } = useHome();
  const [open, setOpen] = useState(false);
  if (!status || !home) return <div className="mode-band"><section className="mode" aria-busy="true"><p className="mode-sum mode-loading">Reading the house…</p></section></div>;
  const shown = shownChanges(status.changes, home.devices);
  const summary = shown.length ? shown.map((c) => `${c.short} ${c.delta}`).join(" · ") : learningLine(status.learning) ?? "No changes";
  const today = home.now.slice(0, 10);
  const next = status.planned.find((p) => p.status === "planned" && p.at.slice(0, 10) === today);
  return (
    <div className="mode-band">
      <section className="mode" aria-label="Right now">
        <button type="button" className="mode-sum" aria-expanded={open} aria-controls="mode-body" onClick={() => setOpen((o) => !o)}>
          <span className="mode-t">
            <span className="mode-k">Right now{status.since ? ` · since ${hhmm(status.since)}` : ""}</span>
            <b className="mode-n">{status.title}</b>
            <span className="sumline">{summary}</span>
          </span>
          <span className="mode-r">
            {next && <span className="mode-next">Next {hhmm(next.at)}</span>}
            <ChevronIcon />
          </span>
        </button>
        {open && (
          <div className="mode-body" id="mode-body">
            {status.why && <p className="why">{status.why}</p>}
            {status.learning && <p className="why">I’m still learning this home: my plans appear after a few days of watching.</p>}
            <div className="mode-sec">
              <span className="al-label">Expected</span>
              <PlanList items={status.planned} now={home.now} onEvent={onEvent} />
            </div>
            {status.notSent.length > 0 && (
              <div className="mode-sec">
                <span className="al-label">Not sent · look-only</span>
                <ul className="pl">
                  {status.notSent.map((p) => (
                    <li key={p.id} className="is-off">
                      <time>{hhmm(p.at)}</time>
                      <span className="pl-w"><b>{p.title}</b><span>Would have: {p.does}</span></span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
