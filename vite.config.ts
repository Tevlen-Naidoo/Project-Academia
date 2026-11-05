// Builds the site's web components (web/src) into web/dist, which the Python site
// builder copies into site/static. See "Working on the site" in the README.
//
// `npm run dev` also gives a local preview with live reload: it builds the pages
// with the Python site builder, serves them, and swaps in the TypeScript sources so
// a saved stylesheet updates the open page at once and a saved component reloads it.
import { spawn } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin, type ViteDevServer } from "vite";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEV_SITE = path.join(HERE, ".cache", "dev-site");
/** Changes here rebuild the pages (templates, page data, the builder itself). */
const PAGE_SOURCES = ["site_builder", "data", "config"].map((p) => path.join(HERE, p));

const TYPES: Record<string, string> = {
	".png": "image/png",
	".js": "text/javascript",
	".webmanifest": "application/manifest+json",
	".json": "application/json",
};

function buildPages(): Promise<boolean> {
	return new Promise((resolve) => {
		const child = spawn("uv", ["run", "python", "-m", "site_builder", "build", "--out", DEV_SITE, "--no-assets"], {
			cwd: HERE,
			stdio: "inherit",
		});
		child.on("error", (err) => {
			console.error(`Couldn't run the site builder (is uv installed?): ${err.message}`);
			resolve(false);
		});
		child.on("exit", (code) => resolve(code === 0));
	});
}

/** The page's built CSS and JS tags, swapped for the TypeScript entry point. */
function devHtml(html: string): string {
	return html
		.replace(/<link rel="stylesheet" href="[^"]*static\/app\.css">\n?/, "")
		.replace(/<script type="module" src="[^"]*static\/app\.js"><\/script>/, '<script type="module" src="/src/main.ts"></script>');
}

function sitePages(): Plugin {
	let rebuild: ReturnType<typeof setTimeout> | undefined;
	return {
		name: "academia-pages",
		apply: "serve",
		async configureServer(server: ViteDevServer) {
			await buildPages();
			server.watcher.add(PAGE_SOURCES);
			server.watcher.on("all", (_event, file) => {
				if (!PAGE_SOURCES.some((dir) => file.startsWith(dir + path.sep))) return;
				if (file.includes(`${path.sep}__pycache__${path.sep}`)) return;
				clearTimeout(rebuild);
				rebuild = setTimeout(async () => {
					if (await buildPages()) server.ws.send({ type: "full-reload" });
				}, 150);
			});
			// Serve the built pages, ahead of Vite's own handlers.
			server.middlewares.use(async (req, res, next) => {
				const url = (req.url ?? "/").split("?")[0];
				let file = path.join(DEV_SITE, decodeURIComponent(url));
				if (!file.startsWith(DEV_SITE)) return next();
				if (existsSync(file) && statSync(file).isDirectory()) file = path.join(file, "index.html");
				if (!existsSync(file)) return next();
				if (!file.endsWith(".html")) {
					// Icons, the manifest and the service worker, as they are.
					res.setHeader("Content-Type", TYPES[path.extname(file)] ?? "application/octet-stream");
					res.end(readFileSync(file));
					return;
				}
				try {
					const html = await server.transformIndexHtml(url, devHtml(readFileSync(file, "utf-8")));
					res.setHeader("Content-Type", "text/html; charset=utf-8");
					res.end(html);
				} catch (err) {
					next(err);
				}
			});
		},
	};
}

export default defineConfig({
	root: path.join(HERE, "web"),
	// Built files load from static/, at any depth, so every path is relative.
	base: "./",
	// Nothing is copied as-is: icons stay with the Python builder (site_builder/static).
	publicDir: false,
	plugins: [sitePages()],
	server: { port: 5173, open: true },
	build: {
		outDir: path.join(HERE, "web", "dist"),
		emptyOutDir: true,
		target: "es2022",
		// Firestore is one big chunk, but it only loads once a page needs the database.
		chunkSizeWarningLimit: 700,
		rollupOptions: {
			input: path.join(HERE, "web", "src", "main.ts"),
			output: {
				entryFileNames: "app.js",
				chunkFileNames: "chunks/[name]-[hash].js",
				assetFileNames: (asset) => (asset.names?.some((n) => n.endsWith(".css")) ? "app.css" : "assets/[name]-[hash][extname]"),
			},
		},
	},
});
