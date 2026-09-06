import { upsertMessage, mergeHistory } from "@/utils/messageIndex";
import { getTaskMessages } from "@/apis/commonApi";
import {
	cancelTask as cancelTaskAPI,
	pauseTask as pauseTaskAPI,
	resumeTask as resumeTaskAPI,
} from "@/apis/commonApi";
import { AgentType } from "@/utils/enum";
import type {
	CoderMessage,
	CoordinatorMessage,
	InterpreterMessage,
	Message,
	ModelerMessage,
	UserMessage,
	WriterMessage,
} from "@/utils/response";
import { TaskWebSocket } from "@/utils/websocket";
import { defineStore } from "pinia";
import { computed, ref } from "vue";

/** 任务管理 Store */
export const useTaskStore = defineStore("task", () => {
	// ---- State ----

	/** 按任务ID分组的消息记录 */
	const messagesByTask = ref<Record<string, Message[]>>({});

	/** 当前活跃的任务ID */
	const currentTaskId = ref<string | null>(null);

	/** 当前任务的消息列表（计算属性） */
	const messages = computed<Message[]>(() => {
		if (!currentTaskId.value) {
			return [];
		}
		return messagesByTask.value[currentTaskId.value] ?? [];
	});
	/** 已处理的消息ID集合（用于去重） */
	const positionsByTask = new Map<string, Map<string, number>>();
	const historyRequests = new Map<string, Map<string, Message>>();

	/** WebSocket 实例 */
	let ws: TaskWebSocket | null = null;
	let connectionGeneration = 0;

	/** WebSocket 连接状态 */
	const wsStatus = ref<
		"connecting" | "connected" | "disconnected" | "reconnecting"
	>("disconnected");

	/** 任务是否正在运行 */
	const isRunning = ref(false);

	/** 当前任务的服务端状态与工作流合同。 */
	const runtimeStatus = ref<string | null>(null);
	const contractVersion = ref<number | null>(null);

	/** 仅历史工作流的活动会话可接受自由消息。 */
	const canSendMessage = computed(
		() =>
			wsStatus.value === "connected" &&
			runtimeStatus.value !== null &&
			contractVersion.value !== 3 && contractVersion.value !== 4 &&
			["starting", "running", "waiting"].includes(runtimeStatus.value),
	);

	// ---- Helpers ----

	/** 类型守卫：判断是否为有效的消息对象 */
	function isMessagePayload(payload: unknown): payload is Message {
		if (!payload || typeof payload !== "object") {
			return false;
		}
		const msgType = Reflect.get(payload, "msg_type");
		return (
			typeof Reflect.get(payload, "id") === "string" &&
			typeof msgType === "string" &&
			["system", "agent", "user", "tool"].includes(msgType)
		);
	}

	/** 设置当前活跃任务 */
	function setCurrentTask(taskId: string) {
		currentTaskId.value = taskId;
		if (typeof window !== "undefined") {
			window.localStorage.setItem("currentTaskId", taskId);
		}
	}

	/** 确保任务的消息桶存在 */
	function ensureTaskBucket(taskId: string) {
		if (!messagesByTask.value[taskId]) {
			messagesByTask.value[taskId] = [];
		}

	}

	/** 追加消息（自动去重和排序） */
	function appendMessage(taskId: string, message: Message) {
        ensureTaskBucket(taskId);
        const rows = messagesByTask.value[taskId];
        let index = positionsByTask.get(taskId);
        if (!index || index.size !== rows.length) {
            index = new Map(rows.map((item, i) => [item.id, i]));
            positionsByTask.set(taskId, index);
        }
        upsertMessage(rows, index, message);
        historyRequests.get(taskId)?.set(message.id, message);
    }

    function mergeMessages(taskId: string, incomingMessages: Message[], live: Message[]) {
        ensureTaskBucket(taskId);
        const rows = mergeHistory(messagesByTask.value[taskId], incomingMessages, live);
        messagesByTask.value[taskId] = rows;
        positionsByTask.set(taskId, new Map(rows.map((item, i) => [item.id, i])));
    }

	// ---- Actions ----

	/** 同步后端运行状态与合同版本。 */
	function setRuntimeStatus(status: string, version: number | null) {
		runtimeStatus.value = status;
		contractVersion.value = version;
		isRunning.value = status === "starting" || status === "running";
	}

	/** 连接 WebSocket 接收实时消息 */
	function connectWebSocket(taskId: string) {
		const generation = ++connectionGeneration;
		historyRequests.clear();
		if (ws) {
			ws.close();
			ws = null;
		}
		setCurrentTask(taskId);
		ensureTaskBucket(taskId);
		runtimeStatus.value = null;
		contractVersion.value = null;
		isRunning.value = false;

		const baseUrl = import.meta.env.VITE_WS_URL;
		const wsUrl = `${baseUrl}/task/${taskId}`;

		ws = new TaskWebSocket(
			wsUrl,
			(data) => {
				if (generation !== connectionGeneration) return;
				if (!isMessagePayload(data)) {
					console.warn("忽略非标准任务消息:", data);
					return;
				}
				appendMessage(taskId, data);
				// 检测任务完成/停止/失败消息
				if (data.msg_type === "system") {
					const msgType = Reflect.get(data, "type");
					if (msgType === "success" || msgType === "error") {
						isRunning.value = false;
					}
				}
			},
			(status) => {
				if (generation !== connectionGeneration) return;
				wsStatus.value = status;
				if (status === "connected") void loadTaskMessages(taskId);
				else historyRequests.delete(taskId);
			},
		);
		ws.connect();
	}

	/** 加载任务的历史消息 */
	async function loadTaskMessages(taskId: string) {
		ensureTaskBucket(taskId);
		const generation = connectionGeneration;
		const live = new Map<string, Message>();
		historyRequests.set(taskId, live);
		try {
			const response = await getTaskMessages(taskId);
			if (generation !== connectionGeneration || historyRequests.get(taskId) !== live) return;
			const validMessages = (response.data ?? []).filter(isMessagePayload);
			mergeMessages(taskId, validMessages, [...live.values()]);
		} catch (error) {
			console.error("加载任务历史消息失败:", error);
		} finally {
			if (historyRequests.get(taskId) === live) historyRequests.delete(taskId);
		}
	}

	/** 关闭 WebSocket 连接 */
	function closeWebSocket() {
		connectionGeneration++;
		historyRequests.clear();
		ws?.close();
		ws = null;
		wsStatus.value = "disconnected";
	}

	/** 持久化暂停当前任务。 */
	async function pauseTask(taskId: string) {
		try {
			const res = await pauseTaskAPI(taskId);
			if (res.data.success) {
				runtimeStatus.value = "paused";
				isRunning.value = false;
			}
			return res.data;
		} catch (error) {
			console.error("暂停任务失败:", error);
			return { success: false, message: "暂停请求失败" };
		}
	}

	/** 从持久状态恢复当前任务。 */
	async function resumeTask(taskId: string) {
		try {
			const res = await resumeTaskAPI(taskId);
			if (res.data.success) {
				runtimeStatus.value = "running";
				isRunning.value = true;
			}
			return res.data;
		} catch (error) {
			console.error("恢复任务失败:", error);
			return { success: false, message: "恢复请求失败" };
		}
	}

	/** 取消正在运行的任务 */
	async function stopTask(taskId: string) {
		try {
			const res = await cancelTaskAPI(taskId);
			if (res.data.success) {
				runtimeStatus.value = "cancelled";
				isRunning.value = false;
			}
			return res.data;
		} catch (error) {
			console.error("取消任务失败:", error);
			return { success: false, message: "取消请求失败" };
		}
	}

	/** 发送消息到当前 Pi 会话 */
	function sendMessage(content: string) {
		const text = content.trim();
		if (!text || !currentTaskId.value || !ws || !canSendMessage.value) {
			return false;
		}
		const message = {
			id: crypto.randomUUID(),
			msg_type: "user" as const,
			content: text,
			created_at: new Date().toISOString(),
		};
		appendMessage(currentTaskId.value, message);
		ws.send({ type: "prompt", id: message.id, message: text });
		isRunning.value = true;
		return true;
	}

	/** 添加用户消息 */
	function addUserMessage(content: string) {
		const taskId = currentTaskId.value ?? "local";
		appendMessage(taskId, {
			id: Date.now().toString(),
			msg_type: "user",
			content: content,
		} as UserMessage);
	}

	/** 下载消息为 JSON 文件 */
	function downloadMessages() {
		const dataStr = `data:text/json;charset=utf-8,${encodeURIComponent(JSON.stringify(messages.value, null, 2))}`;
		const downloadAnchorNode = document.createElement("a");
		downloadAnchorNode.setAttribute("href", dataStr);
		downloadAnchorNode.setAttribute(
			"download",
			`${currentTaskId.value ?? "task"}-messages.json`,
		);
		document.body.appendChild(downloadAnchorNode);
		downloadAnchorNode.click();
		downloadAnchorNode.remove();
	}

	// ---- Computed ----

	/** 聊天消息列表 */
	const chatMessages = computed(() =>
		messages.value.filter((msg) => {
			if (msg.msg_type === "agent" && msg.content) {
				return true;
			}
			if (msg.msg_type === "user") {
				return true;
			}
			if (msg.msg_type === "system") {
				return true;
			}
			// if (msg.msg_type === 'tool' && msg.tool_name === 'execute_code') {
			// return true
			// }
			return false;
		}),
	);

	/** 协调者消息列表 */
	const coordinatorMessages = computed(() =>
		messages.value.filter(
			(msg): msg is CoordinatorMessage =>
				msg.msg_type === "agent" &&
				msg.agent_type === AgentType.COORDINATOR &&
				msg.content != null,
		),
	);

	/** 建模者消息列表 */
	const modelerMessages = computed(() =>
		messages.value.filter(
			(msg): msg is ModelerMessage =>
				msg.msg_type === "agent" &&
				msg.agent_type === AgentType.MODELER &&
				msg.content != null,
		),
	);

	/** 代码手消息列表 */
	const coderMessages = computed(() =>
		messages.value.filter(
			(msg): msg is CoderMessage =>
				msg.msg_type === "agent" &&
				msg.agent_type === AgentType.CODER &&
				msg.content != null,
		),
	);

	/** 论文手消息列表 */
	const writerMessages = computed(() =>
		messages.value.filter(
			(msg): msg is WriterMessage =>
				msg.msg_type === "agent" &&
				msg.agent_type === AgentType.WRITER &&
				msg.content != null,
		),
	);

	/** Pi 工具调用消息 */
	const toolMessages = computed(() =>
		messages.value.filter(
			(msg): msg is InterpreterMessage => msg.msg_type === "tool",
		),
	);

	/** 代码执行工具消息列表 */
	const interpreterMessage = computed(() => toolMessages.value);

	/** 从最新代码手消息中提取文件列表 */
	const files = computed(() => {
		// 反向遍历消息找到最新的文件列表
		for (let i = coderMessages.value.length - 1; i >= 0; i--) {
			const msg = coderMessages.value[i];
			if (
				"files" in msg &&
				msg.files &&
				Array.isArray(msg.files) &&
				msg.files.length > 0
			) {
				console.log("找到文件列表:", msg.files);
				return msg.files;
			}
		}
		// 如果没有找到文件列表，返回空数组
		console.log("没有找到文件列表，返回空数组");
		return [];
	});

	// 初始化连接
	// 如果需要自动连接，可以在这里添加代码
	// 例如：connectWebSocket('default')

	return {
		messages,
		wsStatus,
		isRunning,
		runtimeStatus,
		contractVersion,
		canSendMessage,
		chatMessages,
		coordinatorMessages,
		modelerMessages,
		coderMessages,
		writerMessages,
		interpreterMessage,
		toolMessages,
		files,
		setCurrentTask,
		setRuntimeStatus,
		loadTaskMessages,
		connectWebSocket,
		closeWebSocket,
		stopTask,
		pauseTask,
		resumeTask,
		downloadMessages,
		sendMessage,
		addUserMessage,
	};
});
