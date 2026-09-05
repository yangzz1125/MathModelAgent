import request from "@/utils/request";
import type { Message } from "@/utils/response";

/** 健康检查 */
export function getHelloWorld() {
	return request.get<{ message: string }>("/");
}

/** 获取论文写作顺序 */
export function getWriterSeque() {
	return request.get<{ writer_seque: string[] }>("/writer_seque");
}

/** 获取本机 Pi 可用模型和思考强度 */
export function getPiModels() {
	return request.get<{
		models: {
			id: string;
			provider: string;
			model: string;
			context: string;
			max_output: string;
			thinking: boolean;
			images: boolean;
		}[];
		default_model: string;
		default_thinking: string;
		thinking_levels: string[];
	}>("/models");
}

/** 获取 Pi 任务状态和动态阶段进度 */
export function getTaskStatus(task_id: string) {
	return request.get<{
		task_id: string;
		status:
			| "starting"
			| "running"
			| "paused"
			| "waiting"
			| "completed"
            | "completed_with_warnings"
            | "partial"
			| "cancelled"
			| "failed"
			| "stopped";
		model: string;
		thinking: string;
		profiles: {
			planner: { model: string; thinking: string };
			worker: { model: string; thinking: string };
		} | null;
		current_stage: string | null;
		mode: string | null;
		plan_version: number | null;
		contract_version: number | null;
		paused_at: string | null;
		pause_reason: string | null;
		pause_count: number;
		resume_count: number;
		can_pause: boolean;
		can_resume: boolean;
        delivery_status?: string;
        runtime_metrics?: { prompts?: number; restarts?: number; active_seconds?: number; cleanup_required?: boolean };
        compute_jobs?: number;
        cache_hits?: number;
		started_at: string;
		paper_url: string | null;
		phases: {
			id: string;
			label: string;
			status:
				| "pending"
				| "running"
				| "paused"
				| "completed"
				| "waiting"
				| "failed";
			attempts?: number;
			candidate_repair_attempts?: number;
			local_repair_attempts?: number;
			protocol_attempts?: number;
			review_attempts?: number;
			replan_attempts?: number;
			review_status?: string;
			scientific_status?: string;
			reused_from_version?: number;
			proposal_version?: number;
			method_status?: string;
			spike_budget_seconds?: number;
			last_error?: string;
		}[];
	}>(`/task/${task_id}/status`);
}

export interface TaskSummary {
	task_id: string;
	title: string;
	status: string;
	created_at: string;
	current_stage: string;
	continued_from: string;
}

/** 从服务端持久化工作区读取历史任务。 */
export function getTaskHistory() {
	return request.get<TaskSummary[]>("/projects");
}

/** 获取任务的历史消息 */
export function getTaskMessages(task_id: string) {
	return request.get<Message[]>("/messages", {
		params: {
			task_id,
		},
	});
}

/**
 * 打开工作目录
 * @param task_id 任务ID
 */
export function openFolderAPI(task_id: string) {
	return request.get<{ message: string }>("/open_folder", {
		params: {
			task_id,
		},
	});
}

/**
 * 提交样例任务
 * @param example_id 样例ID
 * @param source 来源
 */
export function exampleAPI(example_id: string, source: string) {
	return request.post<{
		task_id: string;
		status: string;
	}>("/example", {
		example_id,
		source,
	});
}

/** 获取后端和 Redis 服务状态 */
export function getServiceStatus() {
	return request.get<Record<string, { status: string; message: string }>>(
		"/status",
	);
}

/** 持久化暂停当前阶段并终止 Pi 进程树。 */
export function pauseTask(task_id: string) {
	return request.post<{ success: boolean; message: string }>(
		`/modeling/${task_id}/pause`,
	);
}

/** 从 project.json 记录的阶段和模式启动新的 Pi RPC 进程。 */
export function resumeTask(task_id: string) {
	return request.post<{ success: boolean; message: string }>(
		`/modeling/${task_id}/resume`,
	);
}

/**
 * 取消正在运行的任务
 * @param task_id 任务ID
 */
export function cancelTask(task_id: string) {
	return request.post<{ success: boolean; message: string }>(
		`/modeling/${task_id}/cancel`,
	);
}
