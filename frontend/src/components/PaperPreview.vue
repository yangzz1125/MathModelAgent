<script setup lang="ts">
import { getTaskStatus } from "@/apis/commonApi";
import { FileText, RefreshCw } from "lucide-vue-next";
import { onBeforeUnmount, onMounted, ref } from "vue";

// ---- Props ----

const props = defineProps<{ taskId: string }>();

// ---- State ----

const paperUrl = ref<string | null>(null);
const loading = ref(true);
let timer: ReturnType<typeof setInterval> | null = null;

async function refresh() {
	try {
		paperUrl.value = (await getTaskStatus(props.taskId)).data.paper_url;
	} catch (error) {
		console.error("获取论文预览失败:", error);
	} finally {
		loading.value = false;
	}
}

onMounted(() => {
	refresh();
	timer = setInterval(refresh, 5000);
});

onBeforeUnmount(() => {
	if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="h-full min-h-0 p-4">
    <div class="h-full min-h-0 overflow-hidden border bg-white">
      <iframe v-if="paperUrl" :src="paperUrl" title="论文 PDF 预览" class="h-full w-full border-0" />
      <div v-else class="flex h-full items-center justify-center text-gray-500">
        <div class="text-center">
          <RefreshCw v-if="loading" class="mx-auto mb-3 h-6 w-6 animate-spin" />
          <FileText v-else class="mx-auto mb-3 h-8 w-8 text-gray-300" />
          <p class="text-sm">{{ loading ? "正在检查论文产物" : "论文编译完成后将在这里预览" }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
