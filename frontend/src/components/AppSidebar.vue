<script setup lang="ts">
import { computed, ref, onMounted, watch } from "vue";
import { useRouter, useRoute } from "vue-router";
import { Plus, MessageSquare, MoreHorizontal, Trash2, Edit, Search, Check, X, Loader2, Pin } from "lucide-vue-next";
import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarHeader,
	SidebarMenu,
	SidebarMenuItem,
	SidebarMenuButton,
	SidebarMenuAction,
} from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "vue-sonner";
import { useTaskStore } from "@/stores/task";
import UserInfo from "@/components/UserInfo.vue";
import { deleteTask, renameTask } from "@/apis/commonApi";

const router = useRouter();
const route = useRoute();
const taskStore = useTaskStore();

const searchQuery = ref("");
const editingTaskId = ref<string | null>(null);
const editingName = ref("");
const isRenaming = ref(false);

const currentTaskId = computed(() => route.params.id as string);

const filteredTasks = computed(() => {
	const tasks = taskStore.tasks || [];
	if (!searchQuery.value) return tasks;
	return tasks.filter((task: { task_name: string }) =>
		task.task_name?.toLowerCase().includes(searchQuery.value.toLowerCase()),
	);
});

const taskStatusLabels: Record<string, string> = {
	ready: "待启动",
	starting: "启动中",
	running: "运行中",
	pausing: "暂停中",
	paused: "已暂停",
	waiting: "等待中",
	completed: "已完成",
    completed_with_warnings: "已完成（有警告）",
    partial: "部分完成",
	failed: "失败",
	cancelled: "已取消",
	unknown: "未知",
};

onMounted(() => {
	taskStore.fetchAllTasks();
});

watch(
	() => route.fullPath,
	() => {
		taskStore.fetchAllTasks();
	},
);

function handleNewChat() {
	taskStore.clearCurrentTask();
	router.push("/");
}

function handleTaskClick(taskId: string) {
	if (editingTaskId.value === taskId) return;
	router.push(`/task/${taskId}`);
}

async function handleDelete(taskId: string) {
	try {
		await deleteTask(taskId);
		toast.success("删除成功");
		await taskStore.fetchAllTasks();
		if (currentTaskId.value === taskId) {
			router.push("/");
		}
	} catch (error) {
		console.error("删除失败:", error);
		toast.error("删除失败");
	}
}

function startRename(task: any) {
	editingTaskId.value = task.id;
	editingName.value = task.task_name;
}

async function confirmRename(taskId: string) {
	if (!editingName.value.trim()) {
		toast.error("名称不能为空");
		return;
	}
	isRenaming.value = true;
	try {
		await renameTask({
			task_id: taskId,
			new_name: editingName.value,
		});
		toast.success("重命名成功");
		await taskStore.fetchAllTasks();
		editingTaskId.value = null;
	} catch (error) {
		console.error("重命名失败:", error);
		toast.error("重命名失败");
	} finally {
		isRenaming.value = false;
	}
}

function cancelRename() {
	editingTaskId.value = null;
}
</script>

<template>
    <Sidebar class="border-r">
        <SidebarHeader class="p-4 gap-4">
            <div class="flex items-center gap-2 px-2">
                <div class="h-8 w-8 rounded-lg bg-primary/10 flex items-center justify-center text-primary">
                    <span class="font-bold text-xl">M</span>
                </div>
                <span class="font-semibold text-lg tracking-tight">MathModelAgent</span>
            </div>

            <Button @click="handleNewChat" class="w-full justify-start gap-2 shadow-sm" variant="default">
                <Plus class="h-4 w-4" />
                新对话
            </Button>

            <div class="relative">
                <Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input v-model="searchQuery" placeholder="搜索历史记录..." class="pl-9 bg-background/50 h-9" />
            </div>
        </SidebarHeader>

        <SidebarContent class="px-2">
            <div class="text-xs font-medium text-muted-foreground px-4 py-2">最近的对话</div>

            <SidebarMenu v-if="taskStore.isLoadingTasks">
                <SidebarMenuItem v-for="i in 5" :key="i" class="px-2 py-1">
                    <Skeleton class="h-8 w-full" />
                </SidebarMenuItem>
            </SidebarMenu>

            <SidebarMenu v-else>
                <SidebarMenuItem v-for="task in filteredTasks" :key="task.id" class="group/item">
                    <div v-if="editingTaskId === task.id" class="flex items-center gap-1 px-2 py-1">
                        <Input v-model="editingName" class="h-8 text-sm" auto-focus
                            @keydown.enter="confirmRename(task.id)" @keydown.esc="cancelRename" />
                        <Button size="icon" variant="ghost" class="h-8 w-8 shrink-0" @click="confirmRename(task.id)"
                            :disabled="isRenaming">
                            <Loader2 v-if="isRenaming" class="h-3 w-3 animate-spin" />
                            <Check v-else class="h-3 w-3 text-green-600" />
                        </Button>
                        <Button size="icon" variant="ghost" class="h-8 w-8 shrink-0" @click="cancelRename">
                            <X class="h-3 w-3 text-red-500" />
                        </Button>
                    </div>

                    <template v-else>
                        <SidebarMenuButton :is-active="currentTaskId === task.id" @click="handleTaskClick(task.id)"
                            :tooltip="task.task_name" class="h-10 transition-all duration-200 pr-9">
                            <MessageSquare class="h-4 w-4 shrink-0" />
                            <span class="truncate">{{ task.task_name }}</span>
                            <span class="ml-auto text-[10px] shrink-0 text-muted-foreground"
                                :class="{ 'text-green-600': task.status === 'completed',
                                    'text-amber-600': ['completed_with_warnings', 'partial'].includes(task.status), 'text-red-600': task.status === 'failed', 'text-blue-600': task.status === 'running' }">
                                {{ taskStatusLabels[task.status] || task.status }}
                            </span>
                        </SidebarMenuButton>

                        <DropdownMenu>
                            <DropdownMenuTrigger as-child>
                                <SidebarMenuAction
                                    class="opacity-0 group-hover/item:opacity-100 data-[state=open]:opacity-100 transition-opacity">
                                    <MoreHorizontal class="h-4 w-4" />
                                    <span class="sr-only">更多操作</span>
                                </SidebarMenuAction>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="start" side="right" class="w-40">
                                <DropdownMenuItem @click="startRename(task)">
                                    <Edit class="mr-2 h-4 w-4" />
                                    <span>重命名</span>
                                </DropdownMenuItem>
                                <DropdownMenuItem @click="handleDelete(task.id)"
                                    class="text-destructive focus:text-destructive">
                                    <Trash2 class="mr-2 h-4 w-4" />
                                    <span>删除</span>
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </template>
                </SidebarMenuItem>

                <div v-if="filteredTasks.length === 0" class="py-8 text-center text-sm text-muted-foreground">
                    {{ searchQuery ? '没有找到相关对话' : '暂无历史对话' }}
                </div>
            </SidebarMenu>
        </SidebarContent>

        <SidebarFooter class="p-4 border-t bg-background/50">
            <UserInfo />
        </SidebarFooter>
    </Sidebar>
</template>
