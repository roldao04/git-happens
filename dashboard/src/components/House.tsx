import { houseLabel, type HouseView } from "../data/house";

const cls = (base: string, state: string, known: boolean) => `${base} ${state}${known ? "" : " unknown"}`;

export function House({ view }: { view: HouseView }) {
  const { lampOn, doorOpen, plugInUse, temperature } = view;
  return (
    <svg className="house" viewBox="0 0 400 260" role="img" aria-label={houseLabel(view)}>
      <defs>
        <clipPath id="house-inside">
          <rect x="44" y="104" width="312" height="132" />
        </clipPath>
      </defs>
      <path className="house-ground" d="M8 236H392" />
      <path className="house-roof" d="M24 108L200 28L376 108Z" />
      <rect className="house-wall" x="44" y="104" width="312" height="132" />
      {temperature !== null && (
        <text className="house-temp" x="200" y="90" textAnchor="middle">{`${temperature.toFixed(1)} °C`}</text>
      )}

      <g className={`house-window${lampOn ? " lit" : ""}`}>
        <rect x="150" y="124" width="64" height="52" rx="3" />
        <path d="M182 124V176M150 150H214" />
      </g>

      <g className={cls("house-lamp", lampOn ? "on" : "off", lampOn !== null)}>
        {lampOn && (
          <g className="house-glow" clipPath="url(#house-inside)">
            <circle cx="96" cy="146" r="62" />
            <circle cx="96" cy="146" r="34" />
          </g>
        )}
        <path className="house-pole" d="M96 150V226M80 230H112" />
        <path className="house-shade" d="M74 150H118L108 124H84Z" />
      </g>

      <g className={cls("house-plug", plugInUse ? "on" : "off", plugInUse !== null)}>
        <rect className="house-outlet" x="226" y="186" width="16" height="22" rx="3" />
        <path className="house-cable" d="M234 208C234 220 214 222 206 214" />
        <path className="house-shelf" d="M150 226H214" />
        <rect className="house-appliance" x="158" y="194" width="44" height="32" rx="6" />
        <circle className="house-dial" cx="172" cy="210" r="6" />
        {plugInUse && <circle className="house-led" cx="191" cy="204" r="3.5" />}
      </g>

      <g className={cls("house-door", doorOpen ? "open" : "closed", doorOpen !== null)}>
        <rect className="house-frame" x="262" y="140" width="60" height="96" />
        {doorOpen ? (
          <>
            <rect className="house-doorway" x="262" y="140" width="60" height="96" />
            <path className="house-panel" d="M262 140L244 128V248L262 236Z" />
          </>
        ) : (
          <>
            <rect className="house-panel" x="262" y="140" width="60" height="96" />
            <circle className="house-knob" cx="312" cy="190" r="3" />
          </>
        )}
      </g>
    </svg>
  );
}
