import { useEffect, useRef } from "react";
import { useHome } from "../state/HomeProvider";
import { modeWords, needsLook } from "../words";
import { DeviceControl, SensorList, type OnResult } from "./DeviceControl";
import { CloseIcon } from "./icons";

const HIDDEN_TYPES = new Set(["router", "phone"]);

/** Every device of this home: controls for the ones a person can change, a row for the rest. Slides in from the right. */
export function DevicesPanel({ onClose, onResult }: { onClose: () => void; onResult: OnResult }) {
  const { api, home, error, lastOk } = useHome();
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => { close.current?.focus(); }, []);
  if (!home) return null;
  const shown = home.devices.filter((d) => !HIDDEN_TYPES.has(d.type));
  const controlled = shown.filter((d) => d.controls.length > 0);
  const others = shown.filter((d) => d.controls.length === 0);
  const look = needsLook(shown);
  const people = home.people;
  const short = modeWords(api.kind, home)?.short;
  return (
    <div className="layer">
      <div className="al-scrim" onClick={onClose} />
      <section className="sheet side" role="dialog" aria-modal="true" aria-labelledby="dev-title">
        <div className="sheet-head">
          <div>
            <h3 id="dev-title">Devices</h3>
            <p>{look ? `${look} need${look === 1 ? "s" : ""} a look` : "All fine"}{short ? ` · ${short}` : ""}</p>
          </div>
          <button ref={close} className="ib" type="button" aria-label="Close devices" onClick={onClose}><CloseIcon /></button>
        </div>
        {error && lastOk && <p className="stale" role="status">Last reading {new Date(lastOk).toTimeString().slice(0, 5)} · can’t reach Alfred</p>}
        {people && (
          <div className="blk people">
            <span className="al-label">Home</span>
            <p>{people.home.length ? people.home.map((p) => p.name).join(", ") : "Nobody"}{people.guests ? ` · ${people.guests} guest${people.guests > 1 ? "s" : ""}` : ""}</p>
          </div>
        )}
        {controlled.map((d) => <div className="blk" key={d.id}><DeviceControl device={d} onResult={onResult} /></div>)}
        {others.length > 0 && <div className="blk"><SensorList devices={others} /></div>}
      </section>
    </div>
  );
}
