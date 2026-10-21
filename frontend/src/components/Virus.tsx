/** A virion: round capsid with knobbed spikes. Used by the logo and the
 * drifting background. Colour comes from `currentColor`. */
export function Virus({ size = 24, spikes = 10, className }: { size?: number; spikes?: number; className?: string }) {
  const c = 50;
  const arms = Array.from({ length: spikes }, (_, i) => {
    const a = (i / spikes) * Math.PI * 2;
    const x1 = c + Math.cos(a) * 27;
    const y1 = c + Math.sin(a) * 27;
    const x2 = c + Math.cos(a) * 40;
    const y2 = c + Math.sin(a) * 40;
    return (
      <g key={i}>
        <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="currentColor" strokeWidth={4} strokeLinecap="round" />
        <circle cx={x2} cy={y2} r={5} fill="currentColor" />
      </g>
    );
  });
  return (
    <svg viewBox="0 0 100 100" width={size} height={size} className={className} aria-hidden="true">
      {arms}
      <circle cx={c} cy={c} r={25} fill="none" stroke="currentColor" strokeWidth={5} />
      <circle cx={c - 8} cy={c - 6} r={4} fill="currentColor" opacity={0.7} />
      <circle cx={c + 7} cy={c + 3} r={3} fill="currentColor" opacity={0.7} />
      <circle cx={c - 2} cy={c + 10} r={2.5} fill="currentColor" opacity={0.7} />
    </svg>
  );
}
