<template>
  <div class="flex items-center gap-2">
    <div
      v-for="(service, key) in services"
      :key="key"
      class="flex items-center gap-1 px-2 py-1 rounded-md text-xs"
      :class="getStatusClass(service.status)"
    >
      <div
        class="w-2 h-2 rounded-full"
        :class="getStatusDotClass(service.status)"
      ></div>
      <span class="capitalize">{{ key }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { getServiceStatus } from "@/apis/commonApi";
import { useToast } from "@/components/ui/toast/use-toast";
import { onMounted, onUnmounted, ref } from "vue";

// ---- Types ----

/** 单个服务状态 */
interface ServiceStatus {
	status: string;
	message: string;
}

type Services = Record<string, ServiceStatus>;

// ---- Reactive State ----

const { toast } = useToast();

/** 服务状态数据 */
const services = ref<Services>({
	bridge: { status: "unknown", message: "Checking..." },
	pi: { status: "unknown", message: "Checking..." },
});

let statusInterval: ReturnType<typeof setInterval> | null = null;

// ---- Methods ----

/** 获取状态对应的背景和文字样式 */
const getStatusClass = (status: string) => {
	switch (status) {
		case "running":
			return "bg-green-100 text-green-800";
		case "error":
			return "bg-red-100 text-red-800";
		default:
			return "bg-gray-100 text-gray-800";
	}
};

/** 获取状态指示点的颜色样式 */
const getStatusDotClass = (status: string) => {
	switch (status) {
		case "running":
			return "bg-green-500";
		case "error":
			return "bg-red-500";
		default:
			return "bg-gray-400";
	}
};

/** 检查服务状态并处理状态变化 */
const checkStatus = async () => {
	try {
		const response = await getServiceStatus();
		const oldStatus = { ...services.value };
		services.value = response.data as Services;

		// 检查是否有服务状态变化为错误
		for (const key of Object.keys(response.data)) {
			const newStatus = response.data[key].status;
			const oldStatusValue = oldStatus[key]?.status;

			if (newStatus === "error" && oldStatusValue !== "error") {
				toast({
					title: "服务警告",
					description: `${key.toUpperCase()} 服务连接失败: ${response.data[key].message}`,
					variant: "destructive",
				});
			}
		}
	} catch (error) {
		console.error("Failed to check service status:", error);
		toast({
			title: "状态检查失败",
			description: "无法获取服务状态，请检查网络连接",
			variant: "destructive",
		});
	}
};

// ---- Lifecycle Hooks ----

onMounted(() => {
	checkStatus();
	statusInterval = setInterval(checkStatus, 30000);
});

onUnmounted(() => {
	if (statusInterval) {
		clearInterval(statusInterval);
	}
});
</script>