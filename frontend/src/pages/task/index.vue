<script setup lang="ts">
import CoderEditor from "@/components/AgentEditor/CoderEditor.vue";
import ChatArea from "@/components/ChatArea.vue";
import PaperPreview from "@/components/PaperPreview.vue";
import WorkflowPanel from "@/components/WorkflowPanel.vue";
import { Button } from "@/components/ui/button";
import {
	ResizableHandle,
	ResizablePanel,
	ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import FilesSheet from "@/pages/task/components/FileSheet.vue";
import { useTaskStore } from "@/stores/task";
import {
	Download,
	FileText,
	MessageSquare,
	Pause,
	Play,
	Square,
	Workflow,
	Wrench,
} from "lucide-vue-next";
import { onBeforeUnmount, onMounted, ref } from "vue";

// ---- Props ----

const props = defineProps<{ task_id: string }>();

// ---- Reactive State ----

const taskStore = useTaskStore();

/** 运行时长相关状态 */
const startTime = ref<number>(Date.now());
const currentTime = ref<number>(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;

/** 格式化运行时长为可读字符串 */
const formatDuration = (ms: number): string => {
	const seconds = Math.floor(ms / 1000);
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const remainingSeconds = seconds % 60;

	if (hours > 0) {
		return `${hours}h ${minutes}m ${remainingSeconds}s`;
	}
	if (minutes > 0) {
		return `${minutes}m ${remainingSeconds}s`;
	}
	return `${remainingSeconds}s`;
};

/** 运行时长显示值 */
const runningDuration = ref<string>("0s");

/** 是否正在请求任务操作 */
const isStopping = ref(false);
const isPausing = ref(false);
const isResuming = ref(false);
const isPaused = ref(false);

/** 更新运行时长 */
const updateDuration = () => {
	currentTime.value = Date.now();
	runningDuration.value = formatDuration(currentTime.value - startTime.value);
};

/** 处理停止运行 */
async function handleStop() {
	isStopping.value = true;
	await taskStore.stopTask(props.task_id);
	isStopping.value = false;
}

/** 持久化暂停。 */
async function handlePause() {
	isPausing.value = true;
	const result = await taskStore.pauseTask(props.task_id);
	if (result.success) isPaused.value = true;
	isPausing.value = false;
}

/** 从当前持久化阶段恢复。 */
async function handleResume() {
	isResuming.value = true;
	const result = await taskStore.resumeTask(props.task_id);
	if (result.success) isPaused.value = false;
	isResuming.value = false;
}

/** 同步 Pi 任务运行状态 */
function handleRuntimeStatus(status: string, contractVersion: number | null) {
	isPaused.value = status === "paused";
	taskStore.setRuntimeStatus(status, contractVersion);
}

// ---- Lifecycle Hooks ----

onMounted(async () => {
	await taskStore.loadTaskMessages(props.task_id);
	taskStore.connectWebSocket(props.task_id);

	// 开始计时
	timer = setInterval(updateDuration, 1000);
	updateDuration(); // 立即更新一次
});

onBeforeUnmount(() => {
	taskStore.closeWebSocket();
	// 清理计时器
	if (timer) {
		clearInterval(timer);
		timer = null;
	}
});
</script>

<template>
  <div class="fixed inset-0">
    <ResizablePanelGroup direction="horizontal" class="desktop-layout h-full rounded-lg border">
      <ResizablePanel :default-size="40" class="h-full">
        <ChatArea :messages="taskStore.chatMessages" />
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel :default-size="60" class="h-full min-w-0">
        <div class="flex h-full flex-col min-w-0">
          <Tabs default-value="workflow" class="w-full h-full flex flex-col">
            <!-- TODO: Agent 的状态 -->
            <div class="border-b px-4 py-1 flex justify-between">
              <div class="flex items-center gap-4">
                <div class="text-sm text-gray-600">
                  运行时长: <span class="font-mono text-blue-600">{{ runningDuration }}</span>
                </div>
                <div class="flex items-center gap-1.5 text-sm">
                  <span
                    class="inline-block h-2 w-2 rounded-full"
                    :class="{
                      'bg-green-500': taskStore.wsStatus === 'connected',
                      'bg-yellow-500 animate-pulse': taskStore.wsStatus === 'connecting' || taskStore.wsStatus === 'reconnecting',
                      'bg-red-500': taskStore.wsStatus === 'disconnected',
                    }"
                  />
                  <span class="text-gray-500">
                    {{
                      taskStore.wsStatus === 'connected' ? '已连接'
                      : taskStore.wsStatus === 'connecting' ? '连接中'
                      : taskStore.wsStatus === 'reconnecting' ? '重连中'
                      : '未连接'
                    }}
                  </span>
                </div>
                <TabsList>
                  <TabsTrigger value="workflow" class="text-sm">
                    工作流
                  </TabsTrigger>
                  <TabsTrigger value="tools" class="text-sm">
                    工具执行
                  </TabsTrigger>
                  <TabsTrigger value="paper" class="text-sm">
                    论文预览
                  </TabsTrigger>
                </TabsList>
              </div>
              <!--  TODO: 其他选项 -->

              <div class="flex justify-end gap-2 items-center">
                <Button
                  v-if="taskStore.isRunning"
                  variant="outline"
                  :disabled="isPausing"
                  @click="handlePause"
                >
                  <Pause class="h-4 w-4" />
                  {{ isPausing ? "暂停中..." : "暂停" }}
                </Button>
                <Button
                  v-else-if="isPaused"
                  :disabled="isResuming"
                  @click="handleResume"
                >
                  <Play class="h-4 w-4" />
                  {{ isResuming ? "恢复中..." : "继续" }}
                </Button>
                <Button
                  v-if="taskStore.isRunning"
                  variant="destructive"
                  :disabled="isStopping"
                  @click="handleStop"
                >
                  <Square class="h-4 w-4" />
                  {{ isStopping ? "停止中..." : "停止运行" }}
                </Button>
                <Button @click="taskStore.downloadMessages" class="flex justify-end">
                  <Download class="h-4 w-4" />
                  下载消息
                </Button>

                <FilesSheet />

              </div>

            </div>

            <TabsContent value="workflow" class="flex-1 p-1 min-w-0 h-full overflow-hidden">
              <WorkflowPanel :task-id="props.task_id" @status="handleRuntimeStatus" />
            </TabsContent>

            <TabsContent value="tools" class="flex-1 p-1 min-w-0 h-full overflow-hidden">
              <CoderEditor />
            </TabsContent>

            <TabsContent value="paper" class="flex-1 p-1 min-w-0 h-full overflow-hidden">
              <PaperPreview :task-id="props.task_id" />
            </TabsContent>
          </Tabs>
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>

    <Tabs default-value="chat" class="mobile-layout h-full min-w-0 flex-col">
      <header class="shrink-0 border-b bg-white p-2">
        <div class="mb-2 flex items-center justify-between gap-2">
          <div class="flex min-w-0 items-center gap-2 text-xs text-gray-500">
            <span class="inline-block h-2 w-2 shrink-0 rounded-full" :class="{
              'bg-green-500': taskStore.wsStatus === 'connected',
              'bg-yellow-500 animate-pulse': taskStore.wsStatus === 'connecting' || taskStore.wsStatus === 'reconnecting',
              'bg-red-500': taskStore.wsStatus === 'disconnected',
            }" />
            <span class="truncate">{{ runningDuration }} · {{ taskStore.wsStatus === 'connected' ? '已连接' : '连接中' }}</span>
          </div>
          <div class="flex shrink-0 gap-1">
            <Button v-if="taskStore.isRunning" size="icon" variant="outline" :disabled="isPausing"
              title="持久化暂停" @click="handlePause">
              <Pause class="h-4 w-4" />
            </Button>
            <Button v-else-if="isPaused" size="icon" :disabled="isResuming"
              title="继续任务" @click="handleResume">
              <Play class="h-4 w-4" />
            </Button>
            <Button v-if="taskStore.isRunning" size="icon" variant="destructive" :disabled="isStopping"
              title="停止运行" @click="handleStop">
              <Square class="h-4 w-4" />
            </Button>
            <Button size="icon" variant="outline" title="下载消息" @click="taskStore.downloadMessages">
              <Download class="h-4 w-4" />
            </Button>
            <FilesSheet />
          </div>
        </div>
        <TabsList class="grid w-full grid-cols-4">
          <TabsTrigger value="chat" title="对话"><MessageSquare class="h-4 w-4" /></TabsTrigger>
          <TabsTrigger value="workflow" title="工作流"><Workflow class="h-4 w-4" /></TabsTrigger>
          <TabsTrigger value="tools" title="工具执行"><Wrench class="h-4 w-4" /></TabsTrigger>
          <TabsTrigger value="paper" title="论文预览"><FileText class="h-4 w-4" /></TabsTrigger>
        </TabsList>
      </header>

      <TabsContent value="chat" class="min-h-0 flex-1 overflow-hidden p-0">
        <ChatArea :messages="taskStore.chatMessages" />
      </TabsContent>
      <TabsContent value="workflow" class="min-h-0 flex-1 overflow-hidden p-0">
        <WorkflowPanel :task-id="props.task_id" @status="handleRuntimeStatus" />
      </TabsContent>
      <TabsContent value="tools" class="min-h-0 flex-1 overflow-hidden p-0">
        <CoderEditor />
      </TabsContent>
      <TabsContent value="paper" class="min-h-0 flex-1 overflow-hidden p-0">
        <PaperPreview :task-id="props.task_id" />
      </TabsContent>
    </Tabs>

  </div>
</template>

<style scoped>
.desktop-layout {
  display: none !important;
}

.mobile-layout {
  display: flex !important;
}

@media (min-width: 768px) {
  .desktop-layout {
    display: flex !important;
  }

  .mobile-layout {
    display: none !important;
  }
}
</style>
