import type { ReactNode } from "react";
import "./RichText.css";

/**
 * The assistant's answer, laid out instead of raw markdown: a heading opens a
 * card, list items are small tiles, a pipe table is a table, **bold** is a
 * highlight. Built as React elements only (no HTML from the model is ever
 * injected), and anything it doesn't recognize stays plain text.
 */
export function RichText({ text }: { text: string }) {
  const sections: { title: string | null; blocks: Block[] }[] = [{ title: null, blocks: [] }];
  for (const block of parse(text)) {
    if (block.kind === "heading") sections.push({ title: block.text, blocks: [] });
    else sections[sections.length - 1].blocks.push(block);
  }
  return (
    <div className="rich" dir="auto">
      {sections
        .filter((s) => s.title || s.blocks.length)
        .map((s, i) =>
          s.title ? (
            <section key={i} className="rich-card">
              <h4>{inline(s.title)}</h4>
              {s.blocks.map(render)}
            </section>
          ) : (
            <div key={i}>{s.blocks.map(render)}</div>
          ),
        )}
    </div>
  );
}

type Block =
  | { kind: "heading"; text: string }
  | { kind: "para"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "table"; rows: string[][] };

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;

function parse(text: string): Block[] {
  const out: Block[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push({ kind: "para", text: para.join(" ") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const t = line.trim();
    if (!t || /^[-*_]{3,}$/.test(t)) {
      flush();
      continue;
    }
    const h = t.match(/^#{1,4}\s+(.*)$/);
    if (h) {
      flush();
      out.push({ kind: "heading", text: h[1] });
      continue;
    }
    if (t.startsWith("|")) {
      flush();
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells); // skip |---|
        i++;
      }
      i--;
      if (rows.length) out.push({ kind: "table", rows });
      continue;
    }
    if (BULLET.test(line)) {
      flush();
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && BULLET.test(lines[i])) {
        items.push(lines[i].replace(BULLET, ""));
        i++;
      }
      i--;
      out.push({ kind: "list", ordered, items });
      continue;
    }
    para.push(t);
  }
  flush();
  return out;
}

function render(b: Block, i: number): ReactNode {
  switch (b.kind) {
    case "para":
      return (
        <p key={i} className="rich-p">
          {inline(b.text)}
        </p>
      );
    case "list":
      return (
        <ul key={i} className={`rich-tiles${b.ordered ? " ordered" : ""}`}>
          {b.items.map((it, j) => (
            <li key={j} className="rich-tile">
              {b.ordered && <span className="rich-n">{j + 1}</span>}
              <span>{inline(it)}</span>
            </li>
          ))}
        </ul>
      );
    case "table":
      return (
        <div key={i} className="rich-table-wrap">
          <table className="rich-table">
            <thead>
              <tr>
                {b.rows[0].map((c, j) => (
                  <th key={j}>{inline(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.slice(1).map((r, j) => (
                <tr key={j}>
                  {r.map((c, k) => (
                    <td key={k}>{inline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return null;
  }
}

/** **bold** -> highlight, `code` -> mono; everything else is text. */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`([^`]+)`|__(.+?)__/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2] !== undefined) out.push(<code key={m.index}>{m[2]}</code>);
    else out.push(<strong key={m.index} className="rich-hl">{m[1] ?? m[3]}</strong>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  // a lone *emphasis* marker the model left behind: drop the stars only
  return out.map((p) => (typeof p === "string" ? p.replace(/(^|\s)\*(\S[^*]*?)\*(?=\s|$|[.,:;!?])/g, "$1$2") : p));
}
