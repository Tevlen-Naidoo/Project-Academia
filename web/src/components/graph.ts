// The connected papers page: <graph-legend> and <paper-graph layout='{...}'>.
// The layout (positions, edges, ticks) is worked out in Python by
// site_builder/graph_view.py; this only draws it.
import { html, nothing, svg, type PropertyValues } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { LightElement } from "../lib/base";
import type { GraphEdge, GraphLayout, GraphNode } from "../lib/types";

const ROLES: [string, string][] = [
	["pick", "This paper"],
	["read_first", "Read first"],
	["foundation", "Foundation"],
	["background", "Background"],
	["reference", "Other references"],
	["citing", "Cited by"],
];

@customElement("graph-legend")
export class GraphLegend extends LightElement {
	override render() {
		return html`<ul class="legend">
			${ROLES.map(([role, name]) => html`<li><span class="dot role-${role}"></span>${name}</li>`)}
		</ul>`;
	}
}

const hasCitations = (n: { citations: number | null }) => n.citations !== null && n.citations !== undefined;

function nodeTitle(n: GraphNode): string {
	return n.title + (n.year ? ` (${n.year})` : "") + (hasCitations(n) ? ` · ${n.citations} citations` : "");
}

const FOCUS_CHARS = 70;
function short(title: string): string {
	return title.length <= FOCUS_CHARS ? title : title.slice(0, FOCUS_CHARS - 1).trimEnd() + "…";
}

function meta(n: { year: number | null; citations: number | null }): string {
	return [n.year, hasCitations(n) ? `${n.citations} citations` : ""].filter(Boolean).join(" · ");
}

/**
 * Hovering a dot (or tapping it on a touch screen) names it and fades every paper
 * that isn't linked to it. On touch, a second tap on the same dot opens the paper.
 */
@customElement("paper-graph")
export class PaperGraph extends LightElement {
	@property({ type: Object }) layout: GraphLayout | null = null;
	@state() private focusId: string | null = null;

	private near = new Map<string, Set<string>>();
	private lastPointer = "mouse";
	private downAt = 0;

	protected override willUpdate(changed: PropertyValues<this>) {
		if (changed.has("layout") && this.layout) {
			const near = new Map(this.layout.nodes.map((n) => [n.id, new Set([n.id])]));
			for (const e of this.layout.edges) {
				const source = near.get(e.source);
				const target = near.get(e.target);
				if (source && target) {
					source.add(e.target);
					target.add(e.source);
				}
			}
			this.near = near;
		}
	}

	private enter(n: GraphNode, e: PointerEvent) {
		if (e.pointerType === "mouse") this.focusId = n.id;
	}

	private leave(n: GraphNode, e: PointerEvent) {
		if (e.pointerType === "mouse" && this.focusId === n.id) this.focusId = null;
	}

	private down(e: PointerEvent) {
		this.lastPointer = e.pointerType;
		this.downAt = Date.now();
	}

	private keyFocus(n: GraphNode) {
		// Keyboard focus names a dot too; a tap's focus is handled by clickNode().
		if (Date.now() - this.downAt > 500) this.focusId = n.id;
	}

	private clickNode(n: GraphNode, e: MouseEvent) {
		// Touch and pen have no hover: the first tap focuses, the next one opens.
		if (this.lastPointer !== "mouse" && this.focusId !== n.id) {
			e.preventDefault();
			this.focusId = n.id;
		}
	}

	private focusLabel(lay: GraphLayout) {
		const n = lay.nodes.find((x) => x.id === this.focusId);
		if (!n) return nothing;
		const left = n.label_left;
		return svg`<text class="focus-label" x=${left ? n.x - n.r - 6 : n.x + n.r + 6} y=${n.y + 4}
			text-anchor=${left ? "end" : "start"}>${short(n.title)}${n.year ? ` (${n.year})` : ""}</text>`;
	}

	private drawing(lay: GraphLayout) {
		const near = this.focusId ? this.near.get(this.focusId) : undefined;
		const nodeClass = (n: GraphNode) =>
			`node role-${n.role}${near ? (near.has(n.id) ? " lit" : " faded") : ""}${n.id === this.focusId ? " focused" : ""}`;
		const edgeClass = (e: GraphEdge) =>
			`edge${e.strong ? " strong" : ""}${e.builds_on ? " builds-on" : ""}` +
			(near ? (e.source === this.focusId || e.target === this.focusId ? " lit" : " faded") : "");
		return html`
			<div class="graph-scroll">
				<svg
					class="graph${near ? " focusing" : ""}"
					viewBox="0 0 ${lay.width} ${lay.height}"
					role="img"
					aria-label="Citation graph around ${lay.pick.title}"
					@pointerdown=${(e: PointerEvent) => this.down(e)}
					@click=${(e: MouseEvent) => {
						if (!(e.target as Element).closest("a.node")) this.focusId = null;
					}}
				>
					<g class="ticks">
						${lay.ticks.map(
							(t) => svg`
							<line x1=${t.x} x2=${t.x} y1="22" y2=${lay.height - 8}></line>
							<text x=${t.x} y="14" text-anchor="middle">${t.label}</text>`,
						)}
					</g>
					<g class="edges">
						${lay.edges.map(
							(e) => svg`<line class=${edgeClass(e)} x1=${e.x1} y1=${e.y1} x2=${e.x2} y2=${e.y2}></line>`,
						)}
					</g>
					<g class="nodes">
						${lay.nodes.map(
							(n) => svg`
							<a href=${n.href || nothing} class=${nodeClass(n)}
								@pointerenter=${(e: PointerEvent) => this.enter(n, e)}
								@pointerleave=${(e: PointerEvent) => this.leave(n, e)}
								@focus=${() => this.keyFocus(n)}
								@blur=${() => {
									if (this.focusId === n.id) this.focusId = null;
								}}
								@click=${(e: MouseEvent) => this.clickNode(n, e)}>
								<title>${nodeTitle(n)}</title>
								<circle cx=${n.x} cy=${n.y} r=${n.r}></circle>
								${
									n.label
										? svg`<text x=${n.label_left ? n.x - n.r - 4 : n.x + n.r + 4} y=${n.y + 4}
											text-anchor=${n.label_left ? "end" : "start"}>${n.label}</text>`
										: nothing
								}
							</a>`,
						)}
					</g>
					${this.focusLabel(lay)}
				</svg>
			</div>
		`;
	}

	override render() {
		const lay = this.layout;
		if (!lay) return nothing;
		return html`
			<section class="graph-section" id="pick-${lay.pick.rank}">
				<h2><a href=${lay.pick.href}>${lay.pick.title}</a></h2>
				${this.drawing(lay)}
				<details class="graph-list">
					<summary>All ${lay.count} papers in this graph</summary>
					${lay.groups.map(
						(g) => html`
							<h3>${g.name}</h3>
							<ul>
								${g.nodes.map(
									(n) => html`<li><a href=${n.href || nothing}>${n.title}</a> <span class="meta">${meta(n)}</span></li>`,
								)}
							</ul>
						`,
					)}
				</details>
				<p><a href=${lay.back_href}>Back to the paper</a></p>
			</section>
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"graph-legend": GraphLegend;
		"paper-graph": PaperGraph;
	}
}
