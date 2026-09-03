<script setup lang="ts">
import { getTaskStatus } from "@/apis/commonApi";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
	AlertCircle,
	CheckCircle2,
	Circle,
	LoaderCircle,
	PauseCircle,
} from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

// ---- Props ----

const props = defineProps<{ taskId: string }>();
const emit = defineEmits<(event: "status", status: string) => void>();

// ---- State ----

type PhaseStatus =
	| "pending"
	| "running"
	| "paused"
	| "completed"
	| "waiting"
	| "failed";

interface TaskStatus {
	status: string;
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
	started_at: string;
	phases: {
		id: string;
		label: string;
		status: PhaseStatus;
		attempts?: number;
		review_attempts?: number;
		replan_attempts?: number;
		review_status?: string;
		scientific_status?: string;
		last_error?: string;
	}[];
	paper_url: string | null;
}

const status = ref<TaskStatus | null>(null);
let timer: ReturnType<typeof setInterval> | null = null;

const statusLabel = computed(() => {
	switch (status.value?.status) {
		case "running":
		case "starting":
			return "运行中";
		case "paused":
			return "已暂停";
		case "waiting":
			return "等待后续指令";
		case "completed":
			return "已完成";
		case "cancelled":
			return "已停止";
		case "failed":
			return "运行失败";
		default:
			return "未运行";
	}
});

const reviewLabels: Record<string, string> = {
	running: "科学审查中",
	accepted: "科学验收通过",
	accept: "科学验收通过",
	reject: "审查拒绝",
	repairing: "按审查修复",
	replanning: "方法重规划",
	pending: "待科学审查",
};

function reviewLabel(value?: string) {
	return value ? reviewLabels[value] || value : "";
}

async function refresh() {
	try {
		status.value = (await getTaskStatus(props.taskId)).data;
		emit("status", status.value.status);
	} catch (error) {
		console.error("获取 Pi 任务状态失败:", error);
	}
}

onMounted(() => {
	refresh();
	timer = setInterval(refresh, 2000);
});

onBeforeUnmount(() => {
	if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="h-full min-h-0 p-4">
    <div class="flex h-full min-h-0 flex-col border bg-white">
      <header class="flex items-center justify-between border-b px-4 py-3">
        <div>
          <h2 class="text-base font-semibold text-gray-900">MathModelAgent 工作流</h2>
          <p v-if="status?.profiles" class="text-xs text-gray-500">
            规划 {{ status.profiles.planner.model }} · 执行 {{ status.profiles.worker.model }}
            <span v-if="status.plan_version"> · Plan v{{ status.plan_version }}</span>
          </p>
          <p v-else class="text-xs text-gray-500">
            {{ status ? `${status.model} · ${status.thinking}` : "正在连接 Pi" }}
          </p>
        </div>
        <span class="text-sm font-medium" :class="{
          'text-blue-600': status?.status === 'running' || status?.status === 'starting',
          'text-green-600': status?.status === 'completed',
          'text-red-600': status?.status === 'failed',
          'text-amber-600': status?.status === 'paused',
          'text-gray-600': status?.status === 'waiting' || status?.status === 'cancelled',
        }">
          {{ statusLabel }}
        </span>
      </header>

      <ScrollArea class="flex-1 min-h-0">
        <ol class="divide-y px-4">
          <li v-for="(phase, index) in status?.phases || []" :key="phase.id"
            class="grid grid-cols-[32px_1fr_auto] items-center gap-3 py-4">
            <div class="flex h-8 w-8 items-center justify-center">
              <CheckCircle2 v-if="phase.status === 'completed'" class="h-5 w-5 text-green-600" />
              <AlertCircle v-else-if="phase.status === 'waiting' || phase.status === 'failed'"
                class="h-5 w-5 text-red-600" />
              <PauseCircle v-else-if="phase.status === 'paused'" class="h-5 w-5 text-amber-600" />
              <LoaderCircle v-else-if="phase.status === 'running'" class="h-5 w-5 animate-spin text-blue-600" />
              <Circle v-else class="h-5 w-5 text-gray-300" />
            </div>
            <div>
              <div class="text-sm font-medium text-gray-900">{{ phase.label }}</div>
              <div class="text-xs text-gray-500">
                阶段 {{ index + 1 }} / {{ status?.phases.length || 0 }}
                <span v-if="phase.attempts"> · 执行 {{ phase.attempts }}</span>
                <span v-if="phase.review_attempts"> · 审查 {{ phase.review_attempts }}</span>
                <span v-if="phase.replan_attempts"> · 重规划 {{ phase.replan_attempts }}</span>
              </div>
              <div v-if="phase.scientific_status || phase.review_status" class="mt-1 text-xs"
                :class="phase.scientific_status === 'accepted' || phase.review_status === 'accept' ? 'text-green-600' : 'text-amber-600'">
                {{ reviewLabel(phase.scientific_status || phase.review_status) }}
              </div>
              <div v-if="phase.last_error" class="mt-1 line-clamp-2 text-xs text-red-600" :title="phase.last_error">
                {{ phase.last_error }}
              </div>
            </div>
            <span class="text-xs" :class="{
              'text-green-600': phase.status === 'completed',
              'text-blue-600': phase.status === 'running',
              'text-gray-400': phase.status === 'pending',
              'text-red-600': phase.status === 'waiting' || phase.status === 'failed',
              'text-amber-600': phase.status === 'paused',
            }">
              {{ phase.status === 'completed' ? '完成' : phase.status === 'running' ? '执行中' : phase.status === 'paused' ? '已暂停' : phase.status === 'waiting' ? '待处理' : phase.status === 'failed' ? '失败' : '等待' }}
            </span>
          </li>
        </ol>
      </ScrollArea>
    </div>
  </div>
</template>
