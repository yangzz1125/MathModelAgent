<script setup lang="ts">
import { getPiModels } from "@/apis/commonApi";
import {
	type ProjectSummary,
	discardProject,
	initializeProject,
	requestErrorDetail,
	startProject,
} from "@/apis/submitModelingApi";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useTaskStore } from "@/stores/task";
import {
	Database,
	FileArchive,
	FileText,
	FolderOpen,
	LoaderCircle,
	Upload,
} from "lucide-vue-next";
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

// ---- State ----

const router = useRouter();
const taskStore = useTaskStore();
const { toast } = useToast();
const step = ref<"import" | "ready">("import");
const importMode = ref<"folder" | "files">("folder");
const selectedFiles = ref<File[]>([]);
const sourceFolder = ref("");
const project = ref<ProjectSummary | null>(null);
const notes = ref("");
const folderInput = ref<HTMLInputElement | null>(null);
const filesInput = ref<HTMLInputElement | null>(null);
const initializing = ref(false);
const starting = ref(false);
const modelsLoading = ref(true);
const models = ref<Awaited<ReturnType<typeof getPiModels>>["data"]["models"]>(
	[],
);
const thinkingLevels = ref<string[]>([
	"off",
	"minimal",
	"low",
	"medium",
	"high",
	"xhigh",
	"max",
]);

const options = ref({
	competition: "CUMCM",
	language: "Chinese",
	paperEngine: "LaTeX",
	unifiedModel: false,
	plannerModel: "",
	plannerThinking: "high",
	workerModel: "",
	workerThinking: "high",
	problemFile: "",
});

function supportsThinking(modelId: string) {
	return models.value.find((item) => item.id === modelId)?.thinking ?? true;
}

const plannerSupportsThinking = computed(() =>
	supportsThinking(options.value.plannerModel),
);
const workerSupportsThinking = computed(() =>
	supportsThinking(options.value.workerModel),
);

const formattedSize = computed(() => {
	const bytes = project.value?.total_bytes ?? 0;
	return bytes < 1024 * 1024
		? `${(bytes / 1024).toFixed(1)} KB`
		: `${(bytes / 1024 / 1024).toFixed(1)} MB`;
});

// ---- Lifecycle ----

onMounted(async () => {
	try {
		const { data } = await getPiModels();
		models.value = data.models;
		thinkingLevels.value = data.thinking_levels;
		const fallback = data.default_model || data.models[0]?.id || "";
		options.value.plannerModel =
			data.models.find((item) => item.id === "openai/gpt-5.6-sol")?.id ||
			fallback;
		options.value.workerModel =
			data.models.find((item) => item.id === "openai/gpt-5.6-luna")?.id ||
			fallback;
		options.value.plannerThinking = data.default_thinking || "high";
		options.value.workerThinking = data.default_thinking || "high";
	} catch (error) {
		console.error("读取 Pi 模型列表失败:", error);
		toast({
			title: "模型列表读取失败",
			description: "将使用 Pi 当前默认模型",
			variant: "destructive",
		});
	} finally {
		modelsLoading.value = false;
	}
});

watch(
	() => options.value.plannerModel,
	() => {
		if (!plannerSupportsThinking.value) options.value.plannerThinking = "off";
		if (options.value.unifiedModel)
			options.value.workerModel = options.value.plannerModel;
	},
);
watch(
	() => options.value.workerModel,
	() => {
		if (!workerSupportsThinking.value) options.value.workerThinking = "off";
	},
);
watch(
	() => options.value.unifiedModel,
	(unified) => {
		if (unified) {
			options.value.workerModel = options.value.plannerModel;
			options.value.workerThinking = options.value.plannerThinking;
		}
	},
);

// ---- Actions ----

function chooseFiles(event: Event) {
	const input = event.target as HTMLInputElement;
	selectedFiles.value = Array.from(input.files ?? []);
	const firstPath = selectedFiles.value[0]?.webkitRelativePath;
	sourceFolder.value = firstPath?.split("/")[0] ?? "";
}

async function initialize() {
	if (!selectedFiles.value.length) {
		toast({
			title: "请选择赛题",
			description: "选择官方赛题文件夹，或切换到散文件导入",
			variant: "destructive",
		});
		return;
	}
	initializing.value = true;
	try {
		project.value = (
			await initializeProject("", selectedFiles.value, sourceFolder.value)
		).data;
		options.value.problemFile = project.value.problem_file;
		step.value = "ready";
	} catch (error) {
		console.error("初始化项目失败:", error);
		toast({
			title: "初始化失败",
			description: requestErrorDetail(
				error,
				"请检查文件路径、文件大小和 bridge 状态",
			),
			variant: "destructive",
		});
	} finally {
		initializing.value = false;
	}
}

async function resetImport() {
	if (project.value) {
		try {
			await discardProject(project.value.project_id);
		} catch (error) {
			console.error("删除未启动项目失败:", error);
		}
	}
	project.value = null;
	selectedFiles.value = [];
	sourceFolder.value = "";
	step.value = "import";
}

async function start() {
	if (!project.value || !options.value.problemFile) {
		toast({
			title: "未识别主题目",
			description: "请从候选文件中选择主题目",
			variant: "destructive",
		});
		return;
	}
	starting.value = true;
	try {
		const response = await startProject(project.value.project_id, {
			question: notes.value,
			problem_file: options.value.problemFile,
			competition: options.value.competition,
			language: options.value.language,
			paper_engine: options.value.paperEngine,
			planner_model: options.value.plannerModel,
			planner_thinking: options.value.plannerThinking,
			worker_model: options.value.unifiedModel
				? options.value.plannerModel
				: options.value.workerModel,
			worker_thinking: options.value.unifiedModel
				? options.value.plannerThinking
				: options.value.workerThinking,
		});
		taskStore.setCurrentTask(response.data.task_id);
		await router.push(`/task/${response.data.task_id}`);
	} catch (error) {
		console.error("启动 Pi 失败:", error);
		toast({
			title: "启动失败",
			description: requestErrorDetail(
				error,
				"项目仍处于已初始化状态，可以修正配置后重试",
			),
			variant: "destructive",
		});
	} finally {
		starting.value = false;
	}
}
</script>

<template>
  <div class="relative mx-auto w-full max-w-2xl">
    <section class="border bg-white shadow-sm">
      <div v-if="step === 'import'" class="p-6">
        <div class="mb-5 flex rounded-md bg-gray-100 p-1">
          <button type="button" class="flex-1 px-3 py-2 text-sm" :class="{
            'bg-white font-medium shadow-sm': importMode === 'folder',
            'text-gray-500': importMode !== 'folder',
          }" @click="importMode = 'folder'">
            赛题文件夹
          </button>
          <button type="button" class="flex-1 px-3 py-2 text-sm" :class="{
            'bg-white font-medium shadow-sm': importMode === 'files',
            'text-gray-500': importMode !== 'files',
          }" @click="importMode = 'files'">
            散文件
          </button>
        </div>

        <button type="button"
          class="flex min-h-48 w-full flex-col items-center justify-center border-2 border-dashed p-8 text-center transition-colors hover:border-primary/50"
          @click="importMode === 'folder' ? folderInput?.click() : filesInput?.click()">
          <FolderOpen v-if="importMode === 'folder'" class="mb-4 h-8 w-8 text-primary" />
          <Upload v-else class="mb-4 h-8 w-8 text-primary" />
          <span class="text-lg font-medium">
            {{ importMode === 'folder' ? '选择官方赛题文件夹' : '选择题目和附件' }}
          </span>
          <span class="mt-2 text-sm text-gray-500">
            {{ selectedFiles.length ? `已选择 ${selectedFiles.length} 个文件` : 'PDF、Excel、CSV、文档和图片均可导入' }}
          </span>
          <span v-if="sourceFolder" class="mt-1 text-xs text-gray-400">{{ sourceFolder }}</span>
        </button>
        <input ref="folderInput" class="hidden" type="file" webkitdirectory multiple @change="chooseFiles">
        <input ref="filesInput" class="hidden" type="file" multiple
          accept=".txt,.md,.pdf,.csv,.xlsx,.docx,.png,.jpg,.jpeg" @change="chooseFiles">

        <div class="mt-5 flex justify-end">
          <Button :disabled="initializing || !selectedFiles.length" @click="initialize">
            <LoaderCircle v-if="initializing" class="h-4 w-4 animate-spin" />
            {{ initializing ? '初始化中' : '初始化项目' }}
          </Button>
        </div>
      </div>

      <div v-else-if="project" class="p-6">
        <header class="mb-5 flex items-start justify-between gap-4 border-b pb-4">
          <div>
            <div class="flex items-center gap-2 text-green-700">
              <FileArchive class="h-5 w-5" />
              <h2 class="font-semibold">工作区初始化完成</h2>
            </div>
            <p class="mt-1 break-all text-xs text-gray-500">{{ project.workspace }}</p>
          </div>
          <span class="shrink-0 text-xs text-gray-500">{{ project.file_count }} 个文件 · {{ formattedSize }}</span>
        </header>

        <div class="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div class="border p-3">
            <FileText class="mb-2 h-4 w-4 text-blue-600" />
            <div class="text-xs text-gray-500">主题目</div>
            <div class="mt-1 truncate text-sm font-medium">{{ project.problem_file || '待选择' }}</div>
          </div>
          <div class="border p-3">
            <Database class="mb-2 h-4 w-4 text-green-600" />
            <div class="text-xs text-gray-500">数据集</div>
            <div class="mt-1 text-sm font-medium">{{ project.datasets.length }} 个</div>
          </div>
          <div class="border p-3">
            <FileArchive class="mb-2 h-4 w-4 text-gray-600" />
            <div class="text-xs text-gray-500">参考文件</div>
            <div class="mt-1 text-sm font-medium">{{ project.references.length }} 个</div>
          </div>
        </div>

        <div class="space-y-4">
          <Select v-if="project.problem_candidates.length > 1" v-model="options.problemFile">
            <SelectTrigger><SelectValue placeholder="选择主题目" /></SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectLabel>主题目候选</SelectLabel>
                <SelectItem v-for="path in project.problem_candidates" :key="path" :value="path">{{ path }}</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>

          <Textarea v-model="notes" placeholder="补充要求（可选）" class="min-h-20" />

          <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Select v-model="options.competition">
              <SelectTrigger><SelectValue placeholder="竞赛模板" /></SelectTrigger>
              <SelectContent><SelectItem value="CUMCM">全国赛</SelectItem><SelectItem value="MCM">MCM / ICM</SelectItem></SelectContent>
            </Select>
            <Select v-model="options.language">
              <SelectTrigger><SelectValue placeholder="论文语言" /></SelectTrigger>
              <SelectContent><SelectItem value="Chinese">中文</SelectItem><SelectItem value="English">英文</SelectItem></SelectContent>
            </Select>
            <Select v-model="options.paperEngine">
              <SelectTrigger><SelectValue placeholder="排版引擎" /></SelectTrigger>
              <SelectContent><SelectItem value="LaTeX">LaTeX</SelectItem><SelectItem value="Typst">Typst</SelectItem></SelectContent>
            </Select>
          </div>

          <div class="space-y-3 border-t pt-4">
            <label class="flex items-center gap-2 text-sm text-gray-700">
              <input v-model="options.unifiedModel" type="checkbox" class="h-4 w-4">
              规划和执行使用同一模型
            </label>

            <div class="grid grid-cols-1 gap-3 sm:grid-cols-[96px_1fr_140px] sm:items-center">
              <span class="text-sm font-medium text-gray-700">规划 / 审查</span>
              <Select v-model="options.plannerModel" :disabled="modelsLoading || models.length === 0">
                <SelectTrigger><SelectValue :placeholder="modelsLoading ? '读取模型中' : 'Pi 默认模型'" /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>Pi 模型</SelectLabel>
                    <SelectItem v-for="model in models" :key="model.id" :value="model.id">
                      {{ model.id }} · {{ model.context }}
                    </SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
              <Select v-model="options.plannerThinking" :disabled="!plannerSupportsThinking">
                <SelectTrigger><SelectValue placeholder="思考强度" /></SelectTrigger>
                <SelectContent>
                  <SelectItem v-for="level in thinkingLevels" :key="level" :value="level">{{ level }}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div v-if="!options.unifiedModel" class="grid grid-cols-1 gap-3 sm:grid-cols-[96px_1fr_140px] sm:items-center">
              <span class="text-sm font-medium text-gray-700">执行 / 写作</span>
              <Select v-model="options.workerModel" :disabled="modelsLoading || models.length === 0">
                <SelectTrigger><SelectValue placeholder="Pi 默认模型" /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>Pi 模型</SelectLabel>
                    <SelectItem v-for="model in models" :key="model.id" :value="model.id">
                      {{ model.id }} · {{ model.context }}
                    </SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
              <Select v-model="options.workerThinking" :disabled="!workerSupportsThinking">
                <SelectTrigger><SelectValue placeholder="思考强度" /></SelectTrigger>
                <SelectContent>
                  <SelectItem v-for="level in thinkingLevels" :key="level" :value="level">{{ level }}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <footer class="mt-6 flex justify-between">
          <Button variant="outline" :disabled="starting" @click="resetImport">重新选择</Button>
          <Button :disabled="starting || !options.problemFile" @click="start">
            <LoaderCircle v-if="starting" class="h-4 w-4 animate-spin" />
            {{ starting ? '启动 Pi 中' : '开始执行' }}
          </Button>
        </footer>
      </div>
    </section>
  </div>
</template>
