import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SubmissionSummary } from "./api";
import { FormSignature } from "./components/FormSignature";
import { ToastProvider } from "./components/Toast";
import { nextOpenField, showValue, signatureOf } from "./fields";
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

  it("Enter goes to the next non-green field, wrapping around", () => {
    const subs = [sub("a", { id_number: "review" }), sub("b", { sender: "likely", receipt_date: "missing" })];
    expect(nextOpenField(subs, null)).toEqual({ sid: "a", field: "id_number" });
    expect(nextOpenField(subs, { sid: "a", field: "id_number" })).toEqual({ sid: "b", field: "sender" });
    expect(nextOpenField(subs, { sid: "b", field: "sender" })).toEqual({ sid: "b", field: "receipt_date" });
    expect(nextOpenField(subs, { sid: "b", field: "receipt_date" })).toEqual({ sid: "a", field: "id_number" });
    expect(nextOpenField([sub("c", {})], null)).toBeNull();
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
    expect(JSON.parse(init.body)).toEqual({ field: "receipt_date", value: "2026-08-20", submission_ids: ["b", "c"] });
    expect(await screen.findByText("הוחל על 2 טפסים")).toBeTruthy();
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
