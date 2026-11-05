// Loads every web component the site uses. Each page is plain HTML with these
// elements in it, fed the day's data as JSON attributes by the site builder.
import "./components/layout.js";
import "./components/papers.js";
import "./components/board.js";
import "./components/archive.js";
import "./components/graph.js";
import "./components/settings.js";
import { resync } from "./push.js";

// Pages are drawn by the components, so jump to a #pick-N link once they have.
async function jumpToHash() {
  if (!location.hash) return;
  const pending = [...document.querySelectorAll("main *")].filter((el) => el.updateComplete);
  await Promise.all(pending.map((el) => el.updateComplete));
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target) target.scrollIntoView();
}

jumpToHash();
resync();
