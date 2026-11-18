import type { Step } from "../data/words";

export function FoundSteps({ steps }: { steps: Step[] }) {
  return (
    <section aria-labelledby="found">
      <h3 id="found">How Alfred found it</h3>
      <ol className="steps">
        {steps.map((s, i) => (
          <li key={s.title + s.text}>
            <span className="step-n">{i + 1}</span>
            <b>{s.title}</b>
            <p>{s.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
