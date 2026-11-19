// 24×24 stroke icons (decorative: the button around each one carries the label).

const svg = (children: React.ReactNode, className?: string) => (
  <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>{children}</svg>
);

export const MenuIcon = () => svg(<path d="M4 7h16M4 12h16M4 17h10" />);
export const SlidersIcon = () => svg(<><path d="M5 7h9M18 7h1M5 12h3M12 12h7M5 17h11M20 17h-1" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="17" r="2" /></>);
export const CloseIcon = () => svg(<path d="M6 6l12 12M18 6L6 18" />);
export const MicIcon = () => svg(<><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></>);
export const SendIcon = () => svg(<path d="M12 19V5M6 11l6-6 6 6" />);
export const NewChatIcon = () => svg(<><path d="M12 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-6" /><path d="M17.5 3.5l3 3L13 14l-4 1 1-4z" /></>);
export const DotsIcon = () => svg(<><circle cx="5.5" cy="12" r="1.6" className="dot" /><circle cx="12" cy="12" r="1.6" className="dot" /><circle cx="18.5" cy="12" r="1.6" className="dot" /></>);
export const TrashIcon = () => svg(<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12M10.5 11v5M13.5 11v5" />);
export const BoxIcon = () => svg(<path d="M4 6h16v4H4zM5.5 10v8.5h13V10M10 13.5h4" />);
export const ChevronIcon = () => svg(<path d="M6 9l6 6 6-6" />, "chev");
export const GearIcon = () => svg(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></>);

/** Alfred's mark: a cloche on a gloved hand (from the design system; the cloche's colour is --mark-c). */
export function Mark({ size = 32 }: { size?: number }) {
  return (
    <svg className="al-mark" viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <g transform="translate(-0.75 -1.25)">
        <circle className="m-c" cx="35" cy="9.5" r="3" />
        <path className="m-c" d="M17 29A18 18 0 0 1 53 29Z" />
        <rect className="m-c" x="12" y="30.5" width="46" height="3.6" rx="1.8" />
        <path className="m-h" d="M23 37H51Q54.5 37 54.5 40Q54.5 43 51 43H37L29 50.5L19.5 41.5Z" />
        <path className="m-h" d="M17.5 44L26.5 53L19.5 60L10.5 51Z" />
      </g>
    </svg>
  );
}
