import type { ReactNode } from "react";

export function Later({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="later">
      <span className="al-pill">Not built yet</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
