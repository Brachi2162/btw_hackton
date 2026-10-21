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
      if (st && !isGreen(st)) cells.push({ sid: s.id, field: f });
    }
  }
  if (!cells.length) return null;
  if (!from) return cells[0];
  const order = subs.flatMap((s) => ["sender", ...TABLE_FIELDS].map((f) => `${s.id}|${f}`));
  const here = order.indexOf(`${from.sid}|${from.field}`);
  return cells.find((c) => order.indexOf(`${c.sid}|${c.field}`) > here) ?? cells[0];
}
