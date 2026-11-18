export function StillLearning({ day, days }: { day: number; days: number }) {
  const label = `day ${day} of ${days}`;
  return (
    <section className="al-empty" aria-labelledby="still-learning">
      <h3 id="still-learning">Still learning</h3>
      <p>Alfred watches a few normal days before it says what is usual in this home.</p>
      <div className="al-counter" style={{ "--al-days": days }} role="img" aria-label={label}>
        {Array.from({ length: days }, (_, i) => (
          <i key={i} className={i < day ? "done" : undefined} />
        ))}
      </div>
      <span className="al-muted al-tnum">{label}</span>
    </section>
  );
}
