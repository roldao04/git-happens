import type { PlanItem } from "../api/types";
import { useHome } from "../state/HomeProvider";
import { hhmm } from "../words";

/** Alfred's planned "do" decisions, each with Skip (or Undo once skipped). */
export function PlanList({ items, now, onEvent }: { items: PlanItem[]; now: string; onEvent?: (text: string) => void }) {
  const { skip, unskip } = useHome();
  if (!items.length) return <p className="why">Nothing else expected today.</p>;
  const today = now.slice(0, 10);
  return (
    <ul className="pl">
      {items.map((p) => {
        const skipped = p.status === "skipped";
        const when = p.at.slice(0, 10) === today ? "" : "tomorrow · ";
        return (
          <li key={p.id} className={skipped ? "is-off" : undefined}>
            <time>{hhmm(p.at)}</time>
            <span className="pl-w"><b>{p.title}</b><span>{when}{p.does}</span></span>
            <span className="pl-a">
              {skipped ? (
                <>
                  <span className="dim">skipped</span>
                  <button className="al-btn quiet sm" type="button" onClick={async () => { await unskip(p.id); onEvent?.(`${p.title} back on`); }}>Undo</button>
                </>
              ) : (
                <button className="al-btn sm" type="button" aria-label={`Skip ${p.title}`} onClick={async () => { await skip(p.id); onEvent?.(`${p.title} skipped`); }}>Skip</button>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
