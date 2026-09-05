<script setup lang="ts">
import { FileText, RefreshCw } from "lucide-vue-next";

defineProps<{ paperUrl: string | null; loading: boolean; accepted: boolean }>();
</script>

<template>
  <div class="h-full min-h-0 p-4">
    <div class="flex h-full min-h-0 flex-col overflow-hidden border bg-white">
      <p v-if="paperUrl && !accepted" class="shrink-0 border-b px-3 py-2 text-xs text-amber-700">论文草稿，尚未通过最终验收</p>
      <iframe v-if="paperUrl" :src="paperUrl" title="论文 PDF 预览" class="min-h-0 w-full flex-1 border-0" />
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
