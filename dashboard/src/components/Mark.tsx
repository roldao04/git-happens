export function Mark() {
  return (
    <svg className="al-mark" viewBox="0 0 64 64" width="32" height="32" aria-hidden="true">
      <g transform="translate(-0.75 -1.25)">
        <circle cx="35" cy="9.5" r="3" fill="var(--accent)" />
        <path d="M17 29A18 18 0 0 1 53 29Z" fill="var(--accent)" />
        <rect x="12" y="30.5" width="46" height="3.6" rx="1.8" fill="var(--accent)" />
        <path d="M23 37H51Q54.5 37 54.5 40Q54.5 43 51 43H37L29 50.5L19.5 41.5Z" fill="var(--text)" />
        <path d="M17.5 44L26.5 53L19.5 60L10.5 51Z" fill="var(--text)" />
      </g>
    </svg>
  );
}
