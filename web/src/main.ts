// The site's entry point. It loads the stylesheets and every web component; each
// page is plain HTML with these elements in it, fed the day's data as JSON
// attributes by the Python site builder (site_builder/).
import "./styles/index.css";
import "./components/layout";
import "./components/papers";
import "./components/board";
import "./components/archive";
import "./components/graph";
import "./components/settings";
import { resync } from "./lib/push";

// Pages are drawn by the components, so jump to a #pick-N link once they have.
async function jumpToHash() {
	if (!location.hash) return;
	const pending = [...document.querySelectorAll("main *")]
		.map((el) => (el as Partial<{ updateComplete: Promise<boolean> }>).updateComplete)
		.filter((p): p is Promise<boolean> => !!p);
	await Promise.all(pending);
	const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
	target?.scrollIntoView();
}

jumpToHash();
resync();
