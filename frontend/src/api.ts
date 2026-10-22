// Thin client for the FastAPI server (backend/vera/api). Types mirror the
// JSON the endpoints return; the contract itself is backend/vera/models.py.

export type Status = "verified" | "likely" | "review" | "missing" | "human";

export interface FieldSummary {
  values: string[];
  status: Status;
  /** The readers' guess in an empty unreadable field (D-013): shown, not written. */
  guess?: string | null;
}

export interface SubmissionSummary {
  id: string;
  page_ids: string[];
  row_status: Status;
  fields: Record<string, FieldSummary>;
}

export interface Source {
  reader: string;
  model: string | null;
  values: string[];
  page_id: string;
  bbox: [number, number, number, number] | null;
  bbox_approx: boolean;
  evidence_text: string | null;
}

export interface FieldResult extends FieldSummary {
  field: string;
  score: number;
  reasons: string[];
  explanation_he: string;
  sources: Source[];
  alternatives: string[];
  history: { user: string; at: string; old_values: string[]; new_values: string[] }[];
}

export interface Submission {
  id: string;
  batch_id: string;
  page_ids: string[];
  row_status: Status;
  fields: Record<string, FieldResult>;
}

export interface Propagation {
  field: string;
  value: string;
  count: number;
  submission_ids: string[];
  /** "fix": the same misread on other forms; "accept": the same likely value */
  kind?: "fix" | "accept";
  from_values?: string[] | null;
}

export interface Counts {
  pages: Record<string, number>;
  rows: Record<string, number>;
  fields: Record<string, number>;
}

export interface Batch {
  id: string;
  name: string;
  created_at: string;
  ref_date: string;
  status: string;
  locked: boolean;
  demo: boolean;
  counts: Counts;
  submissions: SubmissionSummary[];
  documents: {
    id: string;
    filename: string;
    pages: number;
    done: number;
    failed: number;
    stages: (string | null)[];
    errors: string[];
  }[];
  quality_warnings: { page_id: string; message_he: string; sharpness: number | null }[];
}

export interface BatchListItem {
  id: string;
  name: string;
  created_at: string;
  status: string;
  locked: boolean;
  demo: boolean;
}

export interface Page {
  id: string;
  width: number;
  height: number;
  image: string | null;
  status: string;
}

export interface Evidence {
  submission_id: string;
  field: string;
  field_he: string;
  status: Status;
  values: string[];
  crop: { page_id: string; bbox: [number, number, number, number]; approx: boolean } | null;
  sources: {
    reader: string;
    reader_he: string;
    model: string | null;
    values: string[];
    quote: string | null;
    page_id: string;
    bbox: [number, number, number, number] | null;
  }[];
  options: { value: string; readers: string[] }[];
  checks: { label: string; ok: boolean; detail?: string }[];
  reasons: { code: string; text_he: string }[];
  decision_he: string;
}

export interface Brief {
  id: string;
  batch_id: string;
  name: string;
  sender: string;
  central: string;
  commitment: string;
  row_status: Status;
  signature: { key: string; status: Status }[];
}


export interface Followup {
  id: number;
  kind: "missing_field" | "invalid_id" | "tube_orphan";
  batch_id: string | null;
  submission_id: string | null;
  sender: string | null;
  title_he: string;
  detail_he: string;
  status: "open" | "sent" | "closed";
  created_at: string;
  closed_at: string | null;
  draft: string | null;
  demo: boolean;
  age_hours: number;
  overdue: boolean;
}

export interface Weekly {
  from: string;
  to: string;
  has_demo: boolean;
  stats: {
    hours_saved: number;
    forms: number;
    auto_approved_pct: number;
    tubes_without_form: number;
    fields: number;
    fields_needing_eye: number;
    corrections: number;
    open_followups: number;
    manual_seconds_per_form: number;
  };
  by_sender: { sender: string; forms: number; needs_eye_pct: number; top_reason: string | null; top_reason_he: string }[];
  orphans_by_day: { date: string; day_he: string; count: number }[];
  takeaways: { senders: string; orphans: string };
  worst_sender: string | null;
}

export interface Overview {
  review_waiting: number;
  open_followups: number;
  overdue_followups: number;
  forms_waiting_for_tube: number;
  batches_not_exported: number;
  latest_batch_id: string | null;
  has_demo: boolean;
  vlm_problem: string | null;
  scan_folder: string;
}

export interface WatchStatus {
  enabled: boolean;
  folder: string;
  today_only: boolean;
  recursive: boolean;
  batch_id: string | null;
  interval: number;
  last_check: string | null;
  error: string | null;
  added_today: number;
}

export interface InboxPreview {
  folder: string;
  relevant: number;
  skipped: number;
  files: { path: string; size: number; relevant: boolean; reason: string; reason_he: string }[];
  batch_id?: string | null;
}

export interface Note {
  id: number;
  at: string;
  author: string;
  text: string;
  acked_by: string | null;
  acked_at: string | null;
  demo: boolean;
}

export interface Handover {
  since: string;
  done: { pages_read: number; fields_fixed: number; batches_exported: number; tubes_scanned: number };
  pending: Overview;
  hot_forms: (Brief & { review_fields: number })[];
  notes: Note[];
  open_handover: Note | null;
}

export interface ChatReply {
  reply: string;
  actions: { tool: string; args: Record<string, unknown>; summary_he: string }[];
  tools_used: string[];
  suggestions: string[];
  mode: "model" | "offline";
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

function user(): string {
  try {
    return localStorage.getItem("vera-user") || "lab";
  } catch {
    return "lab";
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: {
      "X-User": encodeURIComponent(user()),
      ...(body !== undefined && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
    },
    body: body instanceof FormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.blob()) as Promise<T>;
}

export const api = {
  overview: () => call<Overview>("GET", "/overview"),
  batches: () => call<BatchListItem[]>("GET", "/batches"),
  batch: (id: string) => call<Batch>("GET", `/batches/${id}`),
  createBatch: (name: string) => call<BatchListItem>("POST", "/batches", { name }),
  upload: (id: string, files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return call<{ added: unknown[]; rejected: { filename: string; error: string }[] }>(
      "POST",
      `/batches/${id}/files`,
      fd,
    );
  },
  inbox: (path: string, today: boolean, recursive: boolean) =>
    call<InboxPreview>(
      "GET",
      `/inbox?${new URLSearchParams({ path, today: String(today), recursive: String(recursive) })}`,
    ),
  watch: () => call<WatchStatus>("GET", "/inbox/watch"),
  setWatch: (folder: string, enabled: boolean, today_only: boolean, recursive: boolean) =>
    call<WatchStatus>("PUT", "/inbox/watch", { folder, enabled, today_only, recursive }),
  importFolder: (path: string, today_only: boolean, recursive: boolean) =>
    call<InboxPreview>("POST", "/inbox/import", { path, today_only, recursive }),
  submission: (id: string) => call<Submission>("GET", `/submissions/${id}`),
  page: (id: string) => call<Page>("GET", `/pages/${id}`),
  accept: (sid: string, field: string) =>
    call<Submission & { propagation: Propagation | null }>("POST", `/submissions/${sid}/fields/${field}/accept`),
  correct: (sid: string, field: string, values: string[], force = false) =>
    call<Submission & { propagation: Propagation | null }>("PATCH", `/submissions/${sid}/fields/${field}`, {
      values,
      force,
    }),
  propagate: (bid: string, p: Propagation) =>
    call<{ applied: string[]; skipped: string[]; counts: Counts }>("POST", `/batches/${bid}/propagate`, {
      field: p.field,
      value: p.value,
      submission_ids: p.submission_ids,
      from_values: p.from_values ?? null,
    }),
  setLocation: (sid: string, field: string, pageId: string, bbox: number[]) =>
    call<Evidence>("PUT", `/submissions/${sid}/fields/${field}/location`, {
      page_id: pageId,
      bbox: bbox.map(Math.round),
    }),
  evidence: (sid: string, field: string) => call<Evidence>("GET", `/submissions/${sid}/fields/${field}/evidence`),
  reviewQueue: (bid: string) =>
    call<{ submission_id: string; page_ids: string[]; field: string; status: Status; values: string[]; explanation_he: string }[]>(
      "GET",
      `/batches/${bid}/review-queue`,
    ),
  exportBatch: (bid: string, force = false) => call<Blob>("POST", `/batches/${bid}/export?force=${force}`),
  followups: () => call<Followup[]>("GET", "/followups"),
  patchFollowup: (id: number, body: Partial<Pick<Followup, "status" | "draft">>) =>
    call<Followup>("PATCH", `/followups/${id}`, body),
  draft: (id: number) => call<{ draft: string; source: string }>("POST", `/followups/${id}/draft`),
  weekly: () => call<Weekly>("GET", "/reports/weekly"),
  letter: (sender: string) => call<{ sender: string; text: string }>("POST", "/reports/letter", { sender }),
  handover: () => call<Handover>("GET", "/handover"),
  note: (text: string) => call<Note>("POST", "/handover/note", { text }),
  ack: (id: number) => call<Note>("POST", `/handover/${id}/ack`),
  chat: (messages: { role: "user" | "assistant"; content: string }[]) =>
    call<ChatReply>("POST", "/assistant/chat", { messages }),
  confirm: (tool: string, args: Record<string, unknown>) =>
    call<{ ok: boolean }>("POST", "/assistant/confirm", { tool, args }),
};

export const imageUrl = (pageId: string, opts: { crop?: number[]; scale?: number; maxSide?: number } = {}) => {
  const q = new URLSearchParams();
  if (opts.crop) q.set("crop", opts.crop.map(Math.round).join(","));
  if (opts.scale) q.set("scale", String(opts.scale));
  if (opts.maxSide) q.set("max_side", String(opts.maxSide));
  const s = q.toString();
  return `/api/pages/${pageId}/image${s ? `?${s}` : ""}`;
};
