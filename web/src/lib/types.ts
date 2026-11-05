// The JSON the Python site builder (site_builder/views.py) hands each component.
// Keep these in step with views.py: a field renamed there should be renamed here.

export type ColumnId = "to_read" | "reading" | "done";

/** A paper's ids; any one of them is enough to match two records of one paper. */
export interface PaperIds {
	arxiv?: string;
	doi?: string;
	s2?: string;
	openalex?: string;
}

export interface Links {
	abstract_page?: string;
	pdf?: string;
	doi?: string;
	code?: string;
}

export interface CardSource {
	kind?: string;
	date?: string;
	pick_id?: string;
}

/** A paper as the board stores it (reader/models.py PaperRef). */
export interface BoardPaper {
	id: string;
	ids: PaperIds;
	title: string;
	year?: number;
	venue?: string;
	citations?: number;
	influential_citations?: number;
	is_review?: boolean;
	tldr?: string;
	links?: Links;
}

export interface BoardCard {
	paper: BoardPaper;
	column: ColumnId;
	source?: CardSource;
	added_on: string;
	started_on?: string;
	finished_on?: string;
}

export interface Board {
	schema_version: number;
	cards: BoardCard[];
	updated_at?: string;
}

/** What <board-button> needs: the paper's ids, the card it adds and where it is now. */
export interface BoardButtonData {
	ids: PaperIds;
	payload: { paper: BoardPaper; source: CardSource };
	column: ColumnId | "";
}

export interface Prerequisite {
	label: "read_first" | "foundation" | "background";
	title: string;
	href: string;
	year: number | null;
	venue: string | null;
	reasons: string[];
	quote: string | null;
	tldr: string | null;
	board: BoardButtonData;
}

export interface Pick {
	rank: number;
	topic_label: string;
	funnel: "fresh" | "waves";
	status: string;
	venue: string | null;
	repick: boolean;
	title: string;
	href: string;
	authors: string;
	reason: string;
	tldr: string | null;
	abstract: string | null;
	links: Links;
	board: BoardButtonData;
	graph: { href: string; count: number } | null;
	prerequisites: Prerequisite[];
}

export interface ArchivePick {
	title: string;
	href: string;
	topic: string;
	topic_label: string;
	funnel: string;
}

export interface ArchiveDay {
	label: string;
	href: string;
	picks: ArchivePick[];
}

/** [tag, label] */
export type Topic = [string, string];

export interface GraphNode {
	id: string;
	role: string;
	x: number;
	y: number;
	r: number;
	label: string;
	label_left: boolean;
	title: string;
	year: number | null;
	citations: number | null;
	href: string;
}

export interface GraphEdge {
	source: string;
	target: string;
	x1: number;
	y1: number;
	x2: number;
	y2: number;
	strong: boolean;
	builds_on: boolean;
}

export interface GraphLayout {
	pick: { rank: number; title: string; href: string };
	back_href: string;
	count: number;
	width: number;
	height: number;
	ticks: { label: string; x: number }[];
	edges: GraphEdge[];
	nodes: GraphNode[];
	groups: {
		name: string;
		nodes: { title: string; href: string; year: number | null; citations: number | null }[];
	}[];
}
