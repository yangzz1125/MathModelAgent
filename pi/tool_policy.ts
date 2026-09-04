import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const REVIEW_TOOLS = ["read", "grep", "find", "ls"];
const WORK_TOOLS = ["read", "bash", "edit", "write"];

export default function mathModelToolPolicy(pi: ExtensionAPI) {
	const activate = (mode: "review" | "work") => {
		const requested = mode === "review" ? REVIEW_TOOLS : WORK_TOOLS;
		const available = new Set(pi.getAllTools().map((tool) => tool.name));
		const active = requested.filter((tool) => available.has(tool));
		pi.setActiveTools(active);
		if (!active.includes("read")) {
			throw new Error("Required Pi read tool is unavailable");
		}
	};

	pi.registerFlag("mathmodel-review", {
		description: "Start MathModelAgent with read-only Reviewer capabilities",
		type: "boolean",
		default: false,
	});

	pi.on("session_start", () => {
		activate(pi.getFlag("mathmodel-review") ? "review" : "work");
	});

	pi.registerCommand("mathmodel-tool-policy", {
		description: "Bridge-owned MathModelAgent tool policy",
		handler: async (args) => {
			const [token, mode, ...extra] = args.trim().split(/\s+/);
			if (
				!token ||
				token !== process.env.MATHMODEL_TOOL_POLICY_TOKEN ||
				extra.length > 0 ||
				!(["review", "work"] as string[]).includes(mode)
			) {
				throw new Error("Unauthorized MathModelAgent tool-policy command");
			}
			activate(mode as "review" | "work");
		},
	});
}
