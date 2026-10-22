import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SubmissionSummary } from "./api";
import { FormSignature } from "./components/FormSignature";
import { ToastProvider } from "./components/Toast";
import { UploadDoneProvider, useUploadDone } from "./components/UploadDone";
import { nextAfterSave, nextOpenField, showValue, signatureOf } from "./fields";
import { PropagationBar } from "./screens/fix";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const sub = (id: string, st: Record<string, string>): SubmissionSummary => ({
  id,
  page_ids: [],
  row_status: "verified",
  fields: Object.fromEntries(
    ["sender", "first_name", "last_name", "id_number", "central_numbers", "commitment_numbers", "receipt_date"].map((f) => [
      f,
      { values: ["x"], status: (st[f] ?? "verified") as never },
    ]),
  ),
});

describe("FormSignature", () => {
  it("draws five arcs in status colours, pulses review, fills the tube dot", () => {
    const { container } = render(
      <FormSignature statuses={["verified", "review", "likely", "missing", "verified"]} tube />,
    );
    const arcs = container.querySelectorAll("path");
    expect(arcs).toHaveLength(5);
    expect(arcs[1].getAttribute("class")).toBe("sig-pulse");
    expect(arcs[0].getAttribute("class")).toBeNull();
    expect(arcs[2].getAttribute("stroke")).toBe("var(--likely)");
    expect(container.querySelector("[data-tube=yes]")).not.toBeNull();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain("ת.ז.: לבדיקה");
  });

  it("shows an empty centre when the tube did not match", () => {
    const { container } = render(<FormSignature statuses={Array(5).fill("verified")} tube={false} />);
    expect(container.querySelector("[data-tube=no]")).not.toBeNull();
  });
});

describe("fields", () => {
  it("name arc takes the worse of first and last name; human counts as green", () => {
    const s = sub("a", { last_name: "missing", id_number: "human" });
    expect(signatureOf(s.fields)).toEqual(["missing", "verified", "verified", "verified", "verified"]);
  });

  it("Enter goes to the next field that waits (yellow/orange), wrapping around", () => {
    const subs = [
      sub("a", { id_number: "review" }),
      sub("b", { sender: "likely", first_name: "missing", receipt_date: "review" }),
    ];
    expect(nextOpenField(subs, null)).toEqual({ sid: "a", field: "id_number" });
    expect(nextOpenField(subs, { sid: "a", field: "id_number" })).toEqual({ sid: "b", field: "sender" });
    // the grey first name is done (D-018): straight to the orange date
    expect(nextOpenField(subs, { sid: "b", field: "sender" })).toEqual({ sid: "b", field: "receipt_date" });
    expect(nextOpenField(subs, { sid: "b", field: "receipt_date" })).toEqual({ sid: "a", field: "id_number" });
    expect(nextOpenField([sub("c", {})], null)).toBeNull();
  });

  it("grey fields count as done: only yellow and orange wait", () => {
    const subs = [sub("a", { sender: "missing", id_number: "review" })];
    expect(nextOpenField(subs, null)).toEqual({ sid: "a", field: "id_number" });
    expect(nextAfterSave(subs, { sid: "a", field: "id_number" })).toBeNull();
  });

  it("after a save, finishes the same form first, even a field before it", () => {
    const subs = [sub("a", { sender: "review", id_number: "review" }), sub("b", { sender: "likely" })];
    expect(nextAfterSave(subs, { sid: "a", field: "id_number" })).toEqual({ sid: "a", field: "sender" });
    expect(nextAfterSave(subs, { sid: "a", field: "sender" })).toEqual({ sid: "a", field: "id_number" });
    const done = [sub("a", { id_number: "review" }), sub("b", { sender: "likely" })];
    expect(nextAfterSave(done, { sid: "a", field: "id_number" })).toEqual({ sid: "b", field: "sender" });
    expect(nextAfterSave([sub("a", { id_number: "review" })], { sid: "a", field: "id_number" })).toBeNull();
  });

  it("shows ISO dates as dd/mm/yyyy", () => {
    expect(showValue("receipt_date", "2026-08-20")).toBe("20/08/2026");
    expect(showValue("id_number", "2026-08-20")).toBe("2026-08-20");
  });
});

describe("PropagationBar", () => {
  it("offers the value to N forms and applies it on the server", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ applied: ["b", "c"], skipped: [], counts: {} }), {
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const done = vi.fn();
    render(
      <ToastProvider>
        <PropagationBar
          batchId="B1"
          prop={{ field: "receipt_date", value: "2026-08-20", count: 2, submission_ids: ["b", "c"] }}
          onDone={done}
        />
      </ToastProvider>,
    );
    expect(screen.getByRole("region").textContent).toContain("אישרת תאריך קבלה 20/08/2026");
    fireEvent.click(screen.getByRole("button", { name: /להחיל על/ }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(2));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/batches/B1/propagate");
    expect(JSON.parse(init.body)).toEqual({
      field: "receipt_date",
      value: "2026-08-20",
      submission_ids: ["b", "c"],
      from_values: null,
    });
    expect(await screen.findByText("הוחל על 2 טפסים")).toBeTruthy();
  });

  it("offers a fix of the same misread and sends the misread along", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ applied: ["b"], skipped: [], counts: {} }), {
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ToastProvider>
        <PropagationBar
          batchId="B1"
          prop={{ field: "sender", value: "מרכז רפואי רבין", count: 1, submission_ids: ["b"], kind: "fix", from_values: ["כללית"] }}
          onDone={() => {}}
        />
      </ToastProvider>,
    );
    expect(screen.getByRole("region").textContent).toContain("אותה טעות (כללית)");
    fireEvent.click(screen.getByRole("button", { name: /לתקן/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).from_values).toEqual(["כללית"]);
  });

  it("'רק כאן' closes without calling the server", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const done = vi.fn();
    render(
      <ToastProvider>
        <PropagationBar batchId="B1" prop={{ field: "receipt_date", value: "2026-08-20", count: 3, submission_ids: [] }} onDone={done} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "רק כאן" }));
    expect(done).toHaveBeenCalledWith(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});


describe("Assistant", () => {
  it("shows the reply to a question without breaking the page", async () => {
    const answer = {"reply": "יש **268 שדות** לבדיקה.\n- 5 בירורים", "actions": [], "tools_used": ["work_overview"], "suggestions": ["מה מחכה לי עכשיו?", "אילו מבחנות הגיעו בלי טופס?", "אילו בירורים פתוחים יותר מיממה?", "איך היה השבוע?"], "mode": "model"};
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify(answer), { headers: { "content-type": "application/json" } }),
    );
    // As in newer Chrome, scrollIntoView returns a Promise (jsdom has none).
    Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as never;
    const { Assistant } = await import("./screens/Assistant");
    render(
      <ToastProvider>
        <Assistant />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText(/שאלי את VERA/));
    fireEvent.change(screen.getByLabelText("שאלה ל-VERA"), { target: { value: "מה מחכה לי?" } });
    fireEvent.click(screen.getByText("שליחה"));
    await waitFor(() => expect(screen.getByText(/268 שדות/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText("שאלה ל-VERA"), { target: { value: "ועוד?" } });
    fireEvent.click(screen.getByText("שליחה")); // re-runs the scroll effect
    await waitFor(() => expect(screen.getAllByText(/268 שדות/)).toHaveLength(2));
    // minimize keeps the conversation, and the button says so
    fireEvent.click(screen.getByLabelText("הקטנה (השיחה נשמרת)"));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByText("חזרה לשיחה"));
    expect(screen.getAllByText(/268 שדות/)).toHaveLength(2);
  });
});

describe("ErrorBoundary", () => {
  it("keeps the rest of the page when one part throws, and offers a retry", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { ErrorBoundary } = await import("./components/ErrorBoundary");
    function Boom(): never {
      throw new Error("boom");
    }
    render(
      <div>
        <p>שאר המסך</p>
        <ErrorBoundary label="הצ'אט">
          <Boom />
        </ErrorBoundary>
      </div>,
    );
    expect(screen.getByText("שאר המסך")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("boom");
    expect(screen.getByText("נסי שוב")).toBeTruthy();
  });
});

describe("RichText", () => {
  it("lays out the model's markdown: cards, tiles, a table, highlights, no stars", async () => {
    const { RichText } = await import("./components/RichText");
    const md = [
      "## מה מחכה",
      "- **268 שדות** לבדיקה",
      "- 5 בירורים *פתוחים*",
      "",
      "| שולח | טפסים |",
      "|---|---|",
      "| שיבא | 3 |",
    ].join("\n");
    const { container } = render(<RichText text={md} />);
    expect(container.querySelector(".rich-card h4")?.textContent).toBe("מה מחכה");
    expect(container.querySelectorAll(".rich-tile")).toHaveLength(2);
    expect(container.querySelector(".rich-hl")?.textContent).toBe("268 שדות");
    expect(container.querySelectorAll(".rich-table td")).toHaveLength(2);
    expect(container.textContent).not.toContain("*");
    expect(container.textContent).not.toContain("|");
  });

  it("never injects HTML from the answer", async () => {
    const { RichText } = await import("./components/RichText");
    const { container } = render(<RichText text={'<img src=x onerror="alert(1)"> **שלום**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img");
  });
});

describe("UploadDone", () => {
  it("says the files are in, plays the chime, and goes away by itself", async () => {
    vi.useFakeTimers();
    const tone = vi.fn();
    class FakeAudio {
      currentTime = 0;
      destination = {};
      createGain = () => ({ gain: { value: 0, setValueAtTime: tone, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect: (x: unknown) => x });
      createOscillator = () => ({ type: "", frequency: { value: 0 }, connect: (x: unknown) => x, start() {}, stop() {} });
      close = () => Promise.resolve();
    }
    vi.stubGlobal("AudioContext", FakeAudio);
    function Go() {
      const done = useUploadDone();
      return <button onClick={() => done({ title: "הקבצים עלו בהצלחה", detail: "2 קבצים" })}>go</button>;
    }
    render(
      <UploadDoneProvider>
        <Go />
      </UploadDoneProvider>,
    );
    fireEvent.click(screen.getByText("go"));
    expect(screen.getByText("הקבצים עלו בהצלחה")).toBeTruthy();
    expect(tone).toHaveBeenCalledTimes(2); // two notes
    act(() => {
      vi.advanceTimersByTime(3300);
    });
    expect(screen.queryByText("הקבצים עלו בהצלחה")).toBeNull();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});

describe("Intake", () => {
  it("picks up a batch still being read when the screen is opened again", async () => {
    const live = {
      id: "b1",
      name: "בוקר 23/10",
      created_at: "2026-10-23T08:00:00",
      ref_date: "2026-10-23",
      status: "processing",
      locked: false,
      demo: false,
      counts: { pages: { processing: 1 } },
      submissions: [],
      documents: [{ id: "d1", filename: "forms.pdf", pages: 3, done: 1, failed: 0, stages: ["read"], errors: [] }],
      quality_warnings: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const body = url.endsWith("/api/batches")
          ? [{ id: "b1", name: live.name, created_at: live.created_at, status: "processing", locked: false, demo: false }]
          : url.includes("/api/batches/b1")
            ? live
            : {};
        return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      }),
    );
    const { Intake } = await import("./screens/Intake");
    render(
      <ToastProvider>
        <Intake />
      </ToastProvider>,
    );
    // the file is still there, with its progress, after coming back
    expect(await screen.findByText("forms.pdf")).toBeTruthy();
  });
});
