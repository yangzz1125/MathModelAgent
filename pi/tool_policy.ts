import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const REVIEW_TOOLS = ["read", "grep", "find", "ls"];
const WORK_TOOLS = ["read", "bash", "edit", "write"];

export default function mathModelToolPolicy(pi: ExtensionAPI) {
	pi.registerFlag("mathmodel-review", {
		description: "Start MathModelAgent with read-only Reviewer capabilities",
		type: "boolean",
		default: false,
	});

	pi.on("session_start", () => {
		if (pi.getFlag("mathmodel-review")) {
			const available = new Set(pi.getAllTools().map((tool) => tool.name));
			pi.setActiveTools(REVIEW_TOOLS.filter((tool) => available.has(tool)));
		}
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
			const requested = mode === "review" ? REVIEW_TOOLS : WORK_TOOLS;
			const available = new Set(pi.getAllTools().map((tool) => tool.name));
			const active = requested.filter((tool) => available.has(tool));
			if (!active.includes("read")) {
				throw new Error("Required Pi read tool is unavailable");
			}
			pi.setActiveTools(active);
		},
	});
}
