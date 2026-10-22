import { imageUrl, type Status } from "../api";
import { useWidth } from "../hooks";
import "./FormViewer.css";

export type Box = { field: string; bbox: [number, number, number, number]; status: Status };

const ZOOM = 3;
const LENS = 300;

/**
 * The form image with two layers:
 * - Lens (עדשת מיקרוסקופ): the page blurred and dimmed, and a 300px circle
 *   showing the original x3 around the selected field. It glides from field
 *   to field.
 * - ConfidenceGlow: a soft glow per field box (green confident, yellow one
 *   source, breathing orange needs an eye). A teal line scans the page while
 *   it is being processed.
 */
export function FormViewer({
  pageId,
  pageW,
  pageH,
  focus,
  boxes = [],
  glow = false,
  scanning = false,
}: {
  pageId: string;
  pageW: number;
  pageH: number;
  /** approx: the reader's rough area, outlined instead of zoomed into. */
  focus?: { bbox: [number, number, number, number]; label: string; approx?: boolean } | null;
  boxes?: Box[];
  glow?: boolean;
  scanning?: boolean;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const scale = pageW ? w / pageW : 0;
  const h = pageH * scale;
  const src = imageUrl(pageId, { maxSide: 1800 });
  const size = Math.min(LENS, w * 0.85 || LENS);
  const r = size / 2;

  let lens: React.ReactNode = null;
  if (focus?.approx && scale) {
    const [x1, y1, x2, y2] = focus.bbox;
    lens = (
      <>
        <div
          className="region"
          style={{ left: x1 * scale, top: y1 * scale, width: (x2 - x1) * scale, height: (y2 - y1) * scale }}
        />
        <div className="lens-label" style={{ left: ((x1 + x2) / 2) * scale, top: Math.max(0, y1 * scale - 30) }} dir="rtl">
          {focus.label} · המיקום משוער
        </div>
      </>
    );
  } else if (focus && scale) {
    const [x1, y1, x2, y2] = focus.bbox;
    const cx = ((x1 + x2) / 2) * scale;
    const cy = ((y1 + y2) / 2) * scale;
    const left = Math.max(0, Math.min(w - size, cx - r));
    const top = Math.max(0, Math.min(Math.max(0, h - size), cy - r));
    lens = (
      <>
        <div
          className="lens"
          style={{
            width: size,
            height: size,
            left,
            top,
            backgroundImage: `url("${src}")`,
            backgroundSize: `${w * ZOOM}px ${h * ZOOM}px`,
            backgroundPosition: `${r - cx * ZOOM}px ${r - cy * ZOOM}px`,
          }}
        />
        <div className="lens-label" style={{ left: left + r, top: top + size + 8 }} dir="rtl">
          <span className="num" dir="ltr">
            ×{ZOOM}
          </span>{" "}
          · {focus.label}
        </div>
      </>
    );
  }

  return (
    <div className="viewer" ref={ref} dir="ltr" style={{ height: h || undefined }}>
      {scale > 0 && (
        <>
          <img className={`viewer-img${focus && !focus.approx ? " dimmed" : ""}`} src={src} alt="תמונת הטופס" width={w} height={h} />
          {glow &&
            boxes
              .filter((b) => b.status !== "missing")
              .map((b) => (
                <div
                  key={b.field}
                  className={`glow glow-${b.status === "human" ? "verified" : b.status}`}
                  style={{
                    left: b.bbox[0] * scale,
                    top: b.bbox[1] * scale,
                    width: (b.bbox[2] - b.bbox[0]) * scale,
                    height: (b.bbox[3] - b.bbox[1]) * scale,
                  }}
                />
              ))}
          {scanning && <div className="scanline" />}
          {lens}
        </>
      )}
    </div>
  );
}
