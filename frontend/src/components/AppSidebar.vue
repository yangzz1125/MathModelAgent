<script setup lang="ts">
import {
	BILLBILL,
	DISCORD,
	GITHUB_LINK,
	QQ_GROUP,
	TWITTER,
	XHS,
} from "@/utils/const";
import NavUser from "./NavUser.vue";
import { getTaskHistory, type TaskSummary } from "@/apis/commonApi";
import { Plus, RefreshCw } from "lucide-vue-next";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
	type SidebarProps,
	SidebarRail,
} from "@/components/ui/sidebar";

// ---- Props ----

const props = defineProps<SidebarProps>();

// ---- Reactive State ----

const route = useRoute();
const history = ref<TaskSummary[]>([]);
const loading = ref(false);
const loaded = ref(false);
const loadError = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
const statusLabels: Record<string, string> = {
	ready: "待启动", starting: "启动中", running: "运行中", paused: "已暂停",
	completed: "已完成", failed: "失败", cancelled: "已取消", waiting: "等待中", stopped: "已停止",
	completed_with_warnings: "已完成（有警告）", partial: "部分完成",
};

async function loadHistory() {
	if (loading.value) return;
	loading.value = true;
	try {
		const response = await getTaskHistory();
		if (!disposed) {
			history.value = response.data;
			loaded.value = true;
			loadError.value = false;
		}
	} catch {
		if (!disposed) loadError.value = true;
	} finally {
		loading.value = false;
	}
}

onMounted(() => {
	void loadHistory();
	timer = setInterval(loadHistory, 15000);
});
onBeforeUnmount(() => {
	disposed = true;
	clearInterval(timer);
});

const socialMedia = [
	{
		name: "QQ",
		url: QQ_GROUP,
		icon: "/qq.svg",
	},
	{
		name: "Twitter",
		url: TWITTER,
		icon: "/twitter.svg",
	},
	{
		name: "GitHub",
		url: GITHUB_LINK,
		icon: "/github.svg",
	},
	{
		name: "哔哩哔哩",
		url: BILLBILL,
		icon: "/bilibili.svg",
	},
	{
		name: "小红书",
		url: XHS,
		icon: "/xiaohongshu.svg",
	},
	{
		name: "Discord",
		url: DISCORD,
		icon: "/discord.svg",
	},
];
</script>

<template>
  <Sidebar v-bind="props">
    <SidebarHeader>
      <!-- 图标 -->
      <div class="flex items-center gap-2 h-15">
        <router-link to="/" class="flex items-center gap-2">
          <img src="@/assets/icon.png" alt="logo" class="w-10 h-10">
          <div class="text-lg font-bold">MathModelAgent</div>
        </router-link>
      </div>
    </SidebarHeader>
    <SidebarContent>
      <SidebarGroup>
        <SidebarGroupLabel>开始</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton as-child :is-active="route.path === '/chat'">
                <router-link to="/chat"><Plus class="h-4 w-4" /><span>开始新任务</span></router-link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
      <SidebarGroup>
        <div class="flex items-center justify-between">
          <SidebarGroupLabel>历史任务<span v-if="loaded" class="ml-1">({{ history.length }})</span></SidebarGroupLabel>
          <button type="button" aria-label="刷新历史任务" title="刷新历史任务" :disabled="loading"
            class="flex h-7 w-7 shrink-0 items-center justify-center rounded hover:bg-sidebar-accent disabled:opacity-50"
            @click="loadHistory">
            <RefreshCw class="h-3.5 w-3.5" :class="{ 'animate-spin': loading }" />
          </button>
        </div>
        <SidebarGroupContent>
          <p v-if="loadError" role="alert" class="px-2 py-2 text-xs text-red-600">历史任务加载失败</p>
          <p v-else-if="!loaded" role="status" class="px-2 py-2 text-xs text-muted-foreground">加载中…</p>
          <p v-else-if="history.length === 0" class="px-2 py-2 text-xs text-muted-foreground">暂无历史任务</p>
          <SidebarMenu>
            <SidebarMenuItem v-for="task in history" :key="task.task_id">
              <SidebarMenuButton as-child class="h-auto py-2" :is-active="route.params.task_id === task.task_id">
                <router-link :to="`/task/${task.task_id}`" :title="`${task.title} · ${task.task_id}`">
                  <span class="min-w-0 flex-1">
                    <span class="block truncate text-sm">{{ task.continued_from ? '续跑 · ' : '' }}{{ task.title }}</span>
                    <span class="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span class="truncate font-mono">{{ task.task_id }}</span>
                      <span class="shrink-0" :class="{ 'text-green-700': task.status === 'completed', 'text-amber-700': ['completed_with_warnings', 'partial'].includes(task.status), 'text-red-600': task.status === 'failed', 'text-blue-600': task.status === 'running' }">{{ statusLabels[task.status] || task.status }}</span>
                    </span>
                  </span>
                </router-link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    </SidebarContent>
    <SidebarRail />
    <SidebarFooter>
      <NavUser />
    </SidebarFooter>
    <SidebarFooter>
      <!-- 展示图标社交媒体  -->
      <div class="flex items-center gap-4 justify-centermb-4 border-t  border-light-purple pt-3">
        <a v-for="item in socialMedia" :href="item.url" target="_blank">
          <img :src="item.icon" :alt="item.name" width="24" height="24" class="icon">
        </a>
      </div>
    </SidebarFooter>
  </Sidebar>
</template>