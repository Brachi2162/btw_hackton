import type { Status, SubmissionSummary } from "./api";

export const FIELD_HE: Record<string, string> = {
  first_name: "שם פרטי",
  last_name: "שם משפחה",
  id_number: "ת.ז.",
  central_numbers: "מספר מרכזי",
  commitment_numbers: "מספר התחייבות",
  receipt_date: "תאריך קבלה",
  sender: "גורם שולח",
};

// Table columns after sender and the signature.
export const TABLE_FIELDS = [
  "first_name",
  "last_name",
  "id_number",
  "central_numbers",
  "commitment_numbers",
  "receipt_date",
] as const;

// Fields with numbers/dates: shown left-to-right in monospace.
export const LTR_FIELDS = new Set(["id_number", "central_numbers", "commitment_numbers", "receipt_date"]);

export const STATUS_HE: Record<Status, string> = {
  verified: "מאומת",
  likely: "סביר",
  review: "לבדיקה",
  missing: "חסר",
  human: "אושר",
};

// Accessible: an icon next to every colour (spec 12).
export const STATUS_ICON: Record<Status, string> = {
  verified: "✓",
  human: "✓",
  likely: "•",
  review: "!",
  missing: "–",
};

export const isGreen = (s: Status) => s === "verified" || s === "human";
/** Nothing left to do on it: green, or grey (nothing on the form, or the
 * secretary marked it for a follow-up). Only yellow and orange wait (D-018). */
export const isDone = (s: Status) => isGreen(s) || s === "missing";

// Ring of the form signature: name, ID, central, receipt date, commitment.
const SIG: [string, string[]][] = [
  ["name", ["first_name", "last_name"]],
  ["id_number", ["id_number"]],
  ["central_numbers", ["central_numbers"]],
  ["receipt_date", ["receipt_date"]],
  ["commitment_numbers", ["commitment_numbers"]],
];
const WORST: Status[] = ["review", "missing", "likely", "human", "verified"];

export function signatureOf(fields: Record<string, { status: Status }>): Status[] {
  return SIG.map(([, keys]) => {
    const st = keys.map((k) => fields[k]?.status ?? "missing");
    const worst = st.reduce((a, b) => (WORST.indexOf(b) < WORST.indexOf(a) ? b : a));
    return worst === "human" ? "verified" : worst;
  });
}

/** Display date from ISO yyyy-mm-dd as dd/mm/yyyy (or dd/mm/yy, short). */
export function showValue(field: string, v: string, short = false): string {
  if (field === "receipt_date" && /^\d{4}-\d{2}-\d{2}$/.test(v)) {
    const [y, m, d] = v.split("-");
    return `${d}/${m}/${short ? y.slice(2) : y}`;
  }
  return v;
}

/** Row-major list of fields that still need an eye, for Enter. */
export function nextOpenField(
  subs: SubmissionSummary[],
  from: { sid: string; field: string } | null,
): { sid: string; field: string } | null {
  const cells: { sid: string; field: string }[] = [];
  for (const s of subs) {
    for (const f of ["sender", ...TABLE_FIELDS]) {
      const st = s.fields[f]?.status;
      if (st && !isDone(st)) cells.push({ sid: s.id, field: f });
    }
  }
  if (!cells.length) return null;
  if (!from) return cells[0];
  const order = subs.flatMap((s) => ["sender", ...TABLE_FIELDS].map((f) => `${s.id}|${f}`));
  const here = order.indexOf(`${from.sid}|${from.field}`);
  return cells.find((c) => order.indexOf(`${c.sid}|${c.field}`) > here) ?? cells[0];
}

/** After a field was saved: the next field of the SAME form that still needs
 * an eye (after it, then before it), so one form is finished before the next;
 * then the next form's. `done` is left out: the list may predate the save. */
export function nextAfterSave(
  subs: SubmissionSummary[],
  done: { sid: string; field: string },
): { sid: string; field: string } | null {
  const order = ["sender", ...TABLE_FIELDS];
  const form = subs.find((s) => s.id === done.sid);
  if (form) {
    const open = order.filter((f) => f !== done.field && form.fields[f] && !isDone(form.fields[f].status));
    const here = order.indexOf(done.field);
    const next = open.find((f) => order.indexOf(f) > here) ?? open[0];
    if (next) return { sid: form.id, field: next };
  }
  const rest = subs.map((s) =>
    s.id === done.sid ? { ...s, fields: { ...s.fields, [done.field]: { values: [], status: "human" as const } } } : s,
  );
  return nextOpenField(rest, done);
}
