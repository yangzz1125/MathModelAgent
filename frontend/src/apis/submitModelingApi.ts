import request from "@/utils/request";
import axios from "axios";

export interface ProjectSummary {
	project_id: string;
	status: "ready";
	workspace: string;
	source_folder: string;
	problem_file: string;
	problem_candidates: string[];
	datasets: string[];
	references: string[];
	file_count: number;
	total_bytes: number;
}

export interface StartProjectOptions {
    workflow_mode?: "balanced" | "strict";
	question: string;
	problem_file: string;
	competition: string;
	language: string;
	paper_engine: string;
	planner_model: string;
	planner_thinking: string;
	worker_model: string;
	worker_thinking: string;
}

export function requestErrorDetail(error: unknown, fallback: string) {
	if (axios.isAxiosError(error)) {
		const detail = error.response?.data?.detail;
		if (typeof detail === "string" && detail.trim()) return detail;
	}
	return fallback;
}

/** Copy selected files into a standardized workspace without starting Pi. */
export function initializeProject(
	question: string,
	files: File[],
	sourceFolder = "",
) {
	const formData = new FormData();
	formData.append("ques_all", question);
	formData.append("source_folder", sourceFolder);
	for (const file of files) {
		formData.append("files", file);
		formData.append("relative_paths", file.webkitRelativePath || file.name);
	}
	return request.post<ProjectSummary>("/projects/init", formData, {
		headers: { "Content-Type": "multipart/form-data" },
		timeout: 120000,
	});
}

/** Freeze the selected configuration and start Pi for a ready project. */
export function startProject(projectId: string, options: StartProjectOptions) {
	return request.post<{ task_id: string; status: string }>(
		`/projects/${projectId}/start`,
		options,
	);
}

export function discardProject(projectId: string) {
	return request.delete<{ success: boolean }>(`/projects/${projectId}`);
}

/** Legacy one-step endpoint retained for external callers. */
export function submitModelingTask(
	problem: {
		ques_all: string;
		comp_template: string;
		language: string;
		paper_engine: string;
		model: string;
		thinking: string;
	},
	files?: File[],
) {
	const formData = new FormData();
	formData.append("ques_all", problem.ques_all);
	formData.append("comp_template", problem.comp_template);
	formData.append("language", problem.language);
	formData.append("paper_engine", problem.paper_engine);
	formData.append("format_output", problem.paper_engine);
	formData.append("model", problem.model);
	formData.append("thinking", problem.thinking);
	for (const file of files ?? []) formData.append("files", file);
	return request.post<{ task_id: string; status: string }>(
		"/modeling",
		formData,
		{
			headers: { "Content-Type": "multipart/form-data" },
			timeout: 120000,
		},
	);
}
