<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { File, Download } from "lucide-vue-next";
import Skeleton from "@/components/ui/skeleton/Skeleton.vue";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";
import PaperPreview from "@/components/PaperPreview.vue";
import FileManager from "@/components/FileManager.vue";
import Main from "@/components/Main.vue";
import AppHeader from "@/components/AppHeader.vue";
import WorkflowPanel from "@/components/WorkflowPanel.vue";
import { useFileStore } from "@/stores/file";
import { useTaskStore } from "@/stores/task";
import { useProjectStore } from "@/stores/project";
import { toast } from "vue-sonner";
import { isAcceptedDelivery } from "@/utils/deliveryStatus";
import { getTaskFiles, getTaskStatus, downloadAllFiles, type TaskStatus } from "@/apis/commonApi";
import { getProject, resumeTask, startProject } from "@/apis/submitModelingApi";

const route = useRoute();
const fileStore = useFileStore();
const taskStore = useTaskStore();
const projectStore = useProjectStore();
const runtime = ref<TaskStatus.Response | null>(null);
const starting = ref(false);
const loadingProject = ref(true);
const isDownloading = ref(false);
const selectedTab = ref("files");
const rightPanelCollapsed = ref(false);
let refreshTimer: ReturnType<typeof setInterval> | null = null;

const taskId = computed(() => route.params.id as string);
const isReady = computed(() => projectStore.project?.id === taskId.value && projectStore.project.status === "ready");
const canResume = computed(() => runtime.value?.can_resume === true);
const paperEngine = computed(() => {
	const engine = projectStore.project?.id === taskId.value
		? projectStore.project.paper_engine
		: undefined;
	return engine || fileStore.taskFiles?.paper_engine || "LaTeX";
});
const hasStartedWorkflow = computed(() => !isReady.value && !!runtime.value?.contract_version);

async function refreshFiles() {
	if (!taskId.value) return;
	try {
		const files = await getTaskFiles(taskId.value);
		fileStore.setTaskFiles(files);
	} catch (error) {
		console.warn("refresh files failed:", error);
	}
}

async function refreshRuntime() {
	if (!taskId.value) return;
	try {
		runtime.value = await getTaskStatus(taskId.value);
		projectStore.project = await getProject(taskId.value);
	} catch (error) {
		console.warn("refresh runtime failed:", error);
	}
}

async function initTask() {
	const id = taskId.value;
	if (!id) return;
	loadingProject.value = true;
	fileStore.setTaskFiles(null);
	runtime.value = null;
	selectedTab.value = "files";
	try {
		projectStore.project = await getProject(id);
		if (!isReady.value) {
			taskStore.switchTask(id);
		}
		await Promise.all([refreshFiles(), refreshRuntime()]);
	} finally {
		loadingProject.value = false;
	}
}

async function startCurrentProject() {
	if (!taskId.value || starting.value) return;
	starting.value = true;
	try {
		const options = projectStore.settings();
		const response = await startProject(taskId.value, options);
		projectStore.project = await getProject(taskId.value);
		taskStore.switchTask(response.task_id);
		await Promise.all([refreshFiles(), refreshRuntime()]);
		toast.success("程序已开始建模");
	} catch (error: any) {
		toast.error(error.response?.data?.detail || error.message || "启动失败");
	} finally {
		starting.value = false;
	}
}

async function resumeCurrentTask() {
	if (!taskId.value || starting.value) return;
	starting.value = true;
	try {
		await resumeTask(taskId.value);
		taskStore.switchTask(taskId.value);
		await Promise.all([refreshFiles(), refreshRuntime()]);
		toast.success("已恢复任务");
	} catch (error: any) {
		toast.error(error.response?.data?.detail || error.message || "恢复失败");
	} finally {
		starting.value = false;
	}
}

async function handleDownloadAll() {
	if (!taskId.value || isDownloading.value) return;
	isDownloading.value = true;
	try {
		const response = await downloadAllFiles(taskId.value);
		const url = window.URL.createObjectURL(new Blob([response.data]));
		const link = document.createElement("a");
		link.href = url;
		link.setAttribute("download", `${taskId.value}.zip`);
		document.body.appendChild(link);
		link.click();
		link.remove();
		window.URL.revokeObjectURL(url);
		toast.success("下载成功");
	} catch (error) {
		console.error("下载失败:", error);
		toast.error("下载失败");
	} finally {
		isDownloading.value = false;
	}
}

onMounted(async () => {
	await initTask();
	refreshTimer = setInterval(() => {
		void refreshRuntime();
		void refreshFiles();
	}, 2500);
});
watch(taskId, initTask);
onUnmounted(() => {
	if (refreshTimer) clearInterval(refreshTimer);
});
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden">
    <AppHeader
      :model="projectStore.selectedModel"
      :think="projectStore.think"
      :language="projectStore.language"
      :paper-engine="projectStore.paperEngine"
      :phase-label="isReady ? '项目已初始化' : runtime?.stage || ''"
      @update:model="projectStore.selectedModel = $event"
      @update:think="projectStore.think = $event"
      @update:language="projectStore.language = $event"
      @update:paper-engine="projectStore.paperEngine = $event"
    >
      <div class="md:hidden">
        <Sheet>
          <SheetTrigger as-child>
            <Button size="sm" variant="ghost" class="flex items-center gap-1.5">
              <File class="h-4 w-4" />
              <span class="sr-only md:not-sr-only">任务详情</span>
            </Button>
          </SheetTrigger>
          <SheetContent side="right" class="w-[400px] sm:w-[540px] p-0 flex flex-col gap-0">
            <SheetTitle class="sr-only">任务详情</SheetTitle>
            <div class="flex h-full min-h-0 flex-col bg-background">
              <div class="flex items-center justify-between h-11 px-4 border-b shrink-0">
                <div class="flex items-center gap-2">
                  <button @click="selectedTab = 'files'" :class="['h-11 px-1 text-sm border-b-2', selectedTab === 'files' ? 'font-medium border-blue-500 text-blue-600' : 'text-muted-foreground border-transparent']">工作区文件</button>
                  <button v-if="hasStartedWorkflow" @click="selectedTab = 'plan'" :class="['h-11 px-1 text-sm border-b-2', selectedTab === 'plan' ? 'font-medium border-blue-500 text-blue-600' : 'text-muted-foreground border-transparent']">执行计划</button>
                </div>
                <button @click="selectedTab = 'preview'" :class="['h-7 px-2 rounded-md text-xs', selectedTab === 'preview' ? 'bg-blue-50 text-blue-700 font-medium' : 'text-muted-foreground hover:bg-muted']">论文预览</button>
              </div>
              <div v-if="selectedTab === 'files'" class="flex-1 min-h-0 flex flex-col">
                <div class="p-2 border-b">
                  <Button variant="outline" size="sm" class="w-full justify-start gap-2" :disabled="isDownloading" @click="handleDownloadAll">
                    <Download class="h-4 w-4" />
                    {{ isDownloading ? '下载中...' : '下载完整工作区' }}
                  </Button>
                </div>
                <FileManager v-if="fileStore.taskFiles" :files="fileStore.taskFiles.files" :task-id="taskId" :markdown-editable="isReady" @refresh="refreshFiles" />
                <div v-else class="p-4 space-y-3">
                  <Skeleton class="h-4 w-full" />
                  <Skeleton class="h-4 w-5/6" />
                  <Skeleton class="h-4 w-4/6" />
                </div>
              </div>
              <WorkflowPanel v-else-if="selectedTab === 'plan' && hasStartedWorkflow" :status="runtime" @open-file="selectedTab = 'files'" />
              <div v-else class="flex-1 min-h-0">
                <PaperPreview
                  v-if="fileStore.taskFiles"
                  :task-id="taskId"
                  :files="fileStore.taskFiles.files"
                  :paper-engine="paperEngine"
                  :accepted="isAcceptedDelivery(runtime?.status)"
                />
              </div>
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </AppHeader>

    <div v-if="isReady && !loadingProject" class="mx-4 mt-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 flex items-center justify-between gap-4">
      <div class="text-sm text-blue-950">
        题目和附件已复制到正式工作区。启动前可在右侧编辑 Markdown，文件角色由程序自动识别。
      </div>
      <Button :disabled="starting" @click="startCurrentProject">
        {{ starting ? '启动中...' : '开始建模' }}
      </Button>
    </div>
    <div v-else-if="canResume" class="mx-4 mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 flex items-center justify-between gap-4">
      <div class="text-sm text-amber-950">任务已暂停，可从当前阶段恢复。</div>
      <Button :disabled="starting" @click="resumeCurrentTask">恢复任务</Button>
    </div>

    <div class="flex flex-1 overflow-hidden relative">
      <div class="flex-1 h-full flex flex-col overflow-hidden">
        <div v-if="loadingProject" class="p-4 space-y-4">
          <Skeleton class="h-4 w-3/4" />
          <Skeleton class="h-4 w-1/2" />
        </div>
        <div v-else-if="isReady" class="flex-1 flex items-center justify-center text-sm text-muted-foreground px-6 text-center">
          工作区已就绪。配置模型、输出语言和论文引擎后开始建模。
        </div>
        <Main v-else />
      </div>

      <div class="hidden md:flex h-full flex-row shrink-0 transition-all duration-200" :class="rightPanelCollapsed ? 'w-10' : 'w-[410px]'">
        <div class="w-10 border-l bg-muted/20 flex flex-col items-center py-2 gap-2">
          <button class="h-7 w-7 rounded-md text-sm text-muted-foreground hover:bg-muted hover:text-foreground" @click="rightPanelCollapsed = !rightPanelCollapsed" :title="rightPanelCollapsed ? '展开右栏' : '收起右栏'">{{ rightPanelCollapsed ? '‹' : '›' }}</button>
          <button class="h-7 w-7 rounded-md hover:bg-muted" title="工作区文件" @click="selectedTab = 'files'; rightPanelCollapsed = false"><File class="h-4 w-4 mx-auto" /></button>
          <button class="h-7 w-7 rounded-md text-xs hover:bg-muted" title="论文预览" @click="selectedTab = 'preview'; rightPanelCollapsed = false">PDF</button>
        </div>
        <div v-if="!rightPanelCollapsed" class="flex-1 border-l flex flex-col h-full min-h-0 bg-background">
          <div class="flex items-center justify-between h-11 px-4 border-b shrink-0">
            <div class="flex items-center gap-3">
              <button @click="selectedTab = 'files'" :class="['h-11 px-1 text-sm border-b-2', selectedTab === 'files' ? 'font-medium border-blue-500 text-blue-600' : 'text-muted-foreground border-transparent']">工作区文件</button>
              <button v-if="hasStartedWorkflow" @click="selectedTab = 'plan'" :class="['h-11 px-1 text-sm border-b-2', selectedTab === 'plan' ? 'font-medium border-blue-500 text-blue-600' : 'text-muted-foreground border-transparent']">执行计划</button>
            </div>
            <Button variant="ghost" size="sm" class="h-7 px-2 text-xs gap-1" :disabled="isDownloading" @click="handleDownloadAll"><Download class="h-3.5 w-3.5" />全部</Button>
          </div>
          <div v-if="selectedTab === 'files'" class="flex-1 min-h-0">
            <FileManager v-if="fileStore.taskFiles" :files="fileStore.taskFiles.files" :task-id="taskId" :markdown-editable="isReady" @refresh="refreshFiles" />
            <div v-else class="p-4 space-y-3"><Skeleton class="h-4 w-full" /><Skeleton class="h-4 w-5/6" /><Skeleton class="h-4 w-4/6" /></div>
          </div>
          <WorkflowPanel v-else-if="selectedTab === 'plan' && hasStartedWorkflow" :status="runtime" @open-file="selectedTab = 'files'" />
          <div v-else class="flex-1 min-h-0">
            <PaperPreview v-if="fileStore.taskFiles" :task-id="taskId" :files="fileStore.taskFiles.files" :paper-engine="paperEngine" :accepted="isAcceptedDelivery(runtime?.status)" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
