import { useEffect, useRef } from "react";

// A 3D virus drawn on a 2D canvas (no WebGL library): a shaded capsid with
// surface texture, spike proteins with knobs, and a shell of particles.
// It turns on its own, with the page scroll and with the pointer.
// `stage` (from the page) fades in the three reader orbits and tightens the
// particles, so the picture follows the story being scrolled.

type V3 = [number, number, number];

const STOPS: [number, number, number][] = [
  [251, 138, 60], // orange
  [236, 72, 153], // pink
  [139, 61, 255], // purple
];

function palette(t: number, light = 1): string {
  const x = Math.min(1, Math.max(0, t)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(x));
  const f = x - i;
  const c = STOPS[i].map((v, k) => {
    const mixed = v + (STOPS[i + 1][k] - v) * f;
    return Math.round(light >= 1 ? mixed + (255 - mixed) * (light - 1) : mixed * light);
  });
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function fibonacci(n: number): V3[] {
  const g = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: n }, (_, i) => {
    const y = 1 - ((i + 0.5) / n) * 2;
    const r = Math.sqrt(1 - y * y);
    return [Math.cos(g * i) * r, y, Math.sin(g * i) * r];
  });
}

function rotate([x, y, z]: V3, yaw: number, pitch: number): V3 {
  const ca = Math.cos(yaw);
  const sa = Math.sin(yaw);
  const x1 = x * ca + z * sa;
  const z1 = -x * sa + z * ca;
  const cb = Math.cos(pitch);
  const sb = Math.sin(pitch);
  return [x1, y * cb - z1 * sb, y * sb + z1 * cb];
}

const LIGHT: V3 = (() => {
  const v: V3 = [-0.45, 0.55, 0.7];
  const n = Math.hypot(...v);
  return [v[0] / n, v[1] / n, v[2] / n];
})();
const lambert = (n: V3) => Math.max(0, n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]);

// deterministic jitter so the virus looks organic but never changes
const rand = (i: number) => {
  const s = Math.sin(i * 12.9898) * 43758.5453;
  return s - Math.floor(s);
};

const SPIKES = fibonacci(46).map((d, i) => ({ d, t: (d[1] + 1) / 2, len: 1.36 + rand(i) * 0.1 }));
const BUMPS = fibonacci(190);
const PARTICLES = Array.from({ length: 70 }, (_, i) => {
  const d = fibonacci(70)[i];
  return { d, r: 1.75 + rand(i + 99) * 0.9, t: rand(i + 7), s: 0.6 + rand(i + 3) * 0.9 };
});
// three readers: barcode and rules, model A, model B
const RINGS = [
  { tilt: 0.35, spin: 0, t: 0.05 },
  { tilt: 1.15, spin: 2.1, t: 0.5 },
  { tilt: -0.85, spin: 4.2, t: 0.95 },
];

export function VirusCanvas({ stage = 0 }: { stage?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef(stage);
  stageRef.current = stage;

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let w = 0;
    let h = 0;
    const fit = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(canvas);

    const pointer = { x: 0, y: 0 };
    const eased = { yaw: 0, pitch: 0.28, rings: 0, pull: 0 };
    const onMove = (e: PointerEvent) => {
      pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
      pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener("pointermove", onMove, { passive: true });

    let visible = true;
    const io = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
    io.observe(canvas);

    let raf = 0;
    const t0 = performance.now();
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (!visible || document.hidden || !w) return;
      const t = (now - t0) / 1000;
      const st = stageRef.current;
      const spin = still ? 0 : t * 0.22;
      const k = still ? 1 : 0.06;
      eased.yaw += (spin + window.scrollY * 0.0032 + pointer.x * 0.7 - eased.yaw) * k;
      eased.pitch += (0.28 + pointer.y * 0.45 + Math.sin(window.scrollY * 0.0015) * 0.2 - eased.pitch) * k;
      eased.rings += ((st >= 2 ? 1 : 0) - eased.rings) * 0.05;
      eased.pull += ((st >= 3 ? 1 : 0) - eased.pull) * 0.04;
      draw(ctx, w, h, eased.yaw, eased.pitch, t, eased.rings, eased.pull, still);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      window.removeEventListener("pointermove", onMove);
    };
  }, []);

  return <canvas ref={ref} className="virus-canvas" aria-hidden="true" />;
}

function draw(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  yaw: number,
  pitch: number,
  t: number,
  rings: number,
  pull: number,
  still: boolean,
) {
  ctx.clearRect(0, 0, w, h);
  const R = Math.min(w, h) * 0.24;
  const cx = w / 2;
  const cy = h / 2 + (still ? 0 : Math.sin(t * 0.9) * R * 0.03);
  const D = 5;
  const project = (p: V3) => {
    const f = D / (D - p[2]);
    return { x: cx + p[0] * R * f, y: cy - p[1] * R * f, f, z: p[2] };
  };

  // soft halo
  const halo = ctx.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * 2.3);
  halo.addColorStop(0, "rgba(236,72,153,0.28)");
  halo.addColorStop(0.5, "rgba(139,61,255,0.12)");
  halo.addColorStop(1, "rgba(139,61,255,0)");
  ctx.fillStyle = halo;
  ctx.fillRect(0, 0, w, h);

  // particles (two passes: behind and in front of the body)
  const parts = PARTICLES.map((p, i) => {
    const r = p.r - pull * (p.r - 1.6) * 0.6;
    const a = rotate(p.d, yaw * 0.6 + t * 0.05 * p.s + i, pitch * 0.5);
    return { ...project([a[0] * r, a[1] * r, a[2] * r]), p };
  });
  const dot = (q: (typeof parts)[number]) => {
    ctx.globalAlpha = 0.25 + 0.5 * ((q.z + 2.6) / 5.2);
    ctx.fillStyle = palette(q.p.t, 1.15);
    ctx.beginPath();
    ctx.arc(q.x, q.y, 1.6 * q.f * q.p.s, 0, Math.PI * 2);
    ctx.fill();
  };

  // reader orbits
  const ringPts = RINGS.map((rg) =>
    Array.from({ length: 73 }, (_, i) => {
      const a = (i / 72) * Math.PI * 2;
      let p: V3 = [Math.cos(a) * 1.95, 0, Math.sin(a) * 1.95];
      p = rotate(p, rg.spin + t * 0.15, rg.tilt);
      return project(rotate(p, yaw * 0.3, pitch * 0.4));
    }),
  );
  const drawRings = (front: boolean) => {
    if (rings < 0.02) return;
    ctx.lineWidth = 1.4;
    RINGS.forEach((rg, r) => {
      const pts = ringPts[r];
      ctx.strokeStyle = palette(rg.t);
      for (let i = 0; i < pts.length - 1; i++) {
        if (pts[i].z >= 0 !== front) continue;
        ctx.globalAlpha = rings * (front ? 0.75 : 0.25);
        ctx.beginPath();
        ctx.moveTo(pts[i].x, pts[i].y);
        ctx.lineTo(pts[i + 1].x, pts[i + 1].y);
        ctx.stroke();
      }
      // the reader itself, travelling on its orbit
      const q = pts[Math.floor(((t * 0.08 + r / 3) % 1) * 72)];
      if (q.z >= 0 === front) {
        ctx.globalAlpha = rings;
        ctx.fillStyle = palette(rg.t, 1.1);
        ctx.shadowColor = palette(rg.t);
        ctx.shadowBlur = 14;
        ctx.beginPath();
        ctx.arc(q.x, q.y, 4.5 * q.f, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    });
    ctx.globalAlpha = 1;
  };

  // spikes
  const spikes = SPIKES.map((s) => {
    const n = rotate(s.d, yaw, pitch);
    const base = project([n[0] * 0.96, n[1] * 0.96, n[2] * 0.96]);
    const tip = project([n[0] * s.len, n[1] * s.len, n[2] * s.len]);
    return { s, n, base, tip, light: 0.72 + 0.5 * lambert(n) };
  }).sort((a, b) => a.tip.z - b.tip.z);
  const drawSpike = (q: (typeof spikes)[number]) => {
    const front = q.n[2] >= 0;
    ctx.globalAlpha = front ? 1 : 0.7;
    ctx.strokeStyle = palette(q.s.t, q.light * 0.88);
    ctx.lineCap = "round";
    ctx.lineWidth = R * 0.035 * q.tip.f;
    ctx.beginPath();
    ctx.moveTo(q.base.x, q.base.y);
    ctx.lineTo(q.tip.x, q.tip.y);
    ctx.stroke();
    const kr = R * 0.085 * q.tip.f;
    const g = ctx.createRadialGradient(q.tip.x - kr * 0.35, q.tip.y - kr * 0.4, kr * 0.1, q.tip.x, q.tip.y, kr);
    g.addColorStop(0, palette(q.s.t, 1.45));
    g.addColorStop(0.45, palette(q.s.t, q.light));
    g.addColorStop(1, palette(q.s.t, 0.68));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(q.tip.x, q.tip.y, kr, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  };

  parts.filter((q) => q.z < 0).forEach(dot);
  ctx.globalAlpha = 1;
  drawRings(false);
  spikes.filter((q) => q.n[2] < 0).forEach(drawSpike);

  // capsid
  const body = ctx.createRadialGradient(cx - R * 0.38, cy - R * 0.42, R * 0.08, cx, cy, R);
  body.addColorStop(0, "#ffd9bf");
  body.addColorStop(0.28, "#fb8a3c");
  body.addColorStop(0.58, "#ec4899");
  body.addColorStop(0.86, "#8b3dff");
  body.addColorStop(1, "#3a1470");
  ctx.fillStyle = body;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.fill();

  // surface texture: bumps foreshortened toward the rim
  for (const b of BUMPS) {
    const n = rotate(b, yaw, pitch);
    if (n[2] < 0.08) continue;
    const p = project(n);
    const r = R * 0.07 * p.f;
    const ang = Math.atan2(-(p.y - cy), p.x - cx);
    ctx.globalAlpha = 0.1 + 0.22 * lambert(n);
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.ellipse(p.x, p.y, Math.max(0.5, r * n[2]), r, -ang, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 0.14 * n[2];
    ctx.strokeStyle = "#2a0f3d";
    ctx.lineWidth = 0.8;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // rim light
  const rim = ctx.createRadialGradient(cx, cy, R * 0.82, cx, cy, R * 1.04);
  rim.addColorStop(0, "rgba(255,255,255,0)");
  rim.addColorStop(0.8, "rgba(255,180,220,0.35)");
  rim.addColorStop(1, "rgba(255,180,220,0)");
  ctx.fillStyle = rim;
  ctx.beginPath();
  ctx.arc(cx, cy, R * 1.04, 0, Math.PI * 2);
  ctx.fill();

  spikes.filter((q) => q.n[2] >= 0).forEach(drawSpike);
  drawRings(true);
  parts.filter((q) => q.z >= 0).forEach(dot);
  ctx.globalAlpha = 1;
}
