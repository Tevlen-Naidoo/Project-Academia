// The connected papers page: <graph-legend> and <paper-graph layout='{...}'>.
// The layout (positions, edges, ticks) is worked out in Python by
// site_builder/graph_view.py; this only draws it.
import { html, nothing, svg } from "../../vendor/lit.js";
import { LightElement } from "../base.js";

const ROLES = [
  ["pick", "This paper"],
  ["read_first", "Read first"],
  ["foundation", "Foundation"],
  ["background", "Background"],
  ["reference", "Other references"],
  ["citing", "Cited by"],
];

export class GraphLegend extends LightElement {
  render() {
    return html`<ul class="legend">${ROLES.map(([role, name]) => html`
      <li><span class="dot role-${role}"></span>${name}</li>`)}</ul>`;
  }
}

function nodeTitle(n) {
  return n.title + (n.year ? ` (${n.year})` : "") +
    (n.citations !== null && n.citations !== undefined ? ` · ${n.citations} citations` : "");
}

const FOCUS_CHARS = 70;
function short(title) {
  return title.length <= FOCUS_CHARS ? title : title.slice(0, FOCUS_CHARS - 1).trimEnd() + "…";
}

function meta(n) {
  return [n.year, n.citations !== null && n.citations !== undefined ? `${n.citations} citations` : ""]
    .filter(Boolean).join(" · ");
}

/**
 * Hovering a dot (or tapping it on a touch screen) names it and fades every paper
 * that isn't linked to it. On touch, a second tap on the same dot opens the paper.
 */
export class PaperGraph extends LightElement {
  static properties = { layout: { type: Object }, _focus: { state: true } };

  constructor() {
    super();
    this._focus = null;
    this._lastPointer = "mouse";
  }

  willUpdate(changed) {
    if (changed.has("layout") && this.layout) {
      const near = new Map(this.layout.nodes.map((n) => [n.id, new Set([n.id])]));
      for (const e of this.layout.edges) {
        if (near.has(e.source) && near.has(e.target)) {
          near.get(e.source).add(e.target);
          near.get(e.target).add(e.source);
        }
      }
      this._near = near;
    }
  }

  _enter(n, e) {
    if (e.pointerType === "mouse") this._focus = n.id;
  }

  _leave(n, e) {
    if (e.pointerType === "mouse" && this._focus === n.id) this._focus = null;
  }

  _down(e) {
    this._lastPointer = e.pointerType;
    this._downAt = Date.now();
  }

  _keyFocus(n) {
    // Keyboard focus names a dot too; a tap's focus is handled by _click.
    if (Date.now() - (this._downAt || 0) > 500) this._focus = n.id;
  }

  _click(n, e) {
    // Touch and pen have no hover: the first tap focuses, the next one opens.
    if (this._lastPointer !== "mouse" && this._focus !== n.id) {
      e.preventDefault();
      this._focus = n.id;
    }
  }

  _focusLabel(lay) {
    const n = lay.nodes.find((x) => x.id === this._focus);
    if (!n) return nothing;
    const left = n.label_left;
    return svg`<text class="focus-label" x=${left ? n.x - n.r - 6 : n.x + n.r + 6} y=${n.y + 4}
      text-anchor=${left ? "end" : "start"}>${short(n.title)}${n.year ? ` (${n.year})` : ""}</text>`;
  }

  _drawing(lay) {
    const near = this._focus && this._near ? this._near.get(this._focus) : null;
    const nodeClass = (n) => `node role-${n.role}${near ? (near.has(n.id) ? " lit" : " faded") : ""}${n.id === this._focus ? " focused" : ""}`;
    const edgeClass = (e) => `edge${e.strong ? " strong" : ""}${e.builds_on ? " builds-on" : ""}` +
      (near ? (e.source === this._focus || e.target === this._focus ? " lit" : " faded") : "");
    return html`
      <div class="graph-scroll">
        <svg class="graph${near ? " focusing" : ""}" viewBox="0 0 ${lay.width} ${lay.height}" role="img"
          aria-label="Citation graph around ${lay.pick.title}"
          @pointerdown=${(e) => this._down(e)}
          @click=${(e) => { if (!e.target.closest("a.node")) this._focus = null; }}>
          <g class="ticks">${lay.ticks.map((t) => svg`
            <line x1=${t.x} x2=${t.x} y1="22" y2=${lay.height - 8}></line>
            <text x=${t.x} y="14" text-anchor="middle">${t.label}</text>`)}
          </g>
          <g class="edges">${lay.edges.map((e) => svg`
            <line class=${edgeClass(e)}
              x1=${e.x1} y1=${e.y1} x2=${e.x2} y2=${e.y2}></line>`)}
          </g>
          <g class="nodes">${lay.nodes.map((n) => svg`
            <a href=${n.href || nothing} class=${nodeClass(n)}
              @pointerenter=${(e) => this._enter(n, e)} @pointerleave=${(e) => this._leave(n, e)}
              @focus=${() => this._keyFocus(n)} @blur=${() => { if (this._focus === n.id) this._focus = null; }}
              @click=${(e) => this._click(n, e)}>
              <title>${nodeTitle(n)}</title>
              <circle cx=${n.x} cy=${n.y} r=${n.r}></circle>
              ${n.label ? svg`<text x=${n.label_left ? n.x - n.r - 4 : n.x + n.r + 4} y=${n.y + 4}
                text-anchor=${n.label_left ? "end" : "start"}>${n.label}</text>` : nothing}
            </a>`)}
          </g>
          ${this._focusLabel(lay)}
        </svg>
      </div>`;
  }

  render() {
    const lay = this.layout;
    if (!lay) return nothing;
    return html`
      <section class="graph-section" id="pick-${lay.pick.rank}">
        <h2><a href=${lay.pick.href}>${lay.pick.title}</a></h2>
        ${this._drawing(lay)}
        <details class="graph-list">
          <summary>All ${lay.count} papers in this graph</summary>
          ${lay.groups.map((g) => html`
            <h3>${g.name}</h3>
            <ul>${g.nodes.map((n) => html`
              <li><a href=${n.href || nothing}>${n.title}</a> <span class="meta">${meta(n)}</span></li>`)}
            </ul>`)}
        </details>
        <p><a href=${lay.back_href}>Back to the paper</a></p>
      </section>`;
  }
}

customElements.define("graph-legend", GraphLegend);
customElements.define("paper-graph", PaperGraph);
