<script setup lang="ts">
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";

const modelValue = defineModel({
	type: Boolean,
	default: false,
});
</script>

<template>
  <Dialog :open="modelValue" @update:open="modelValue = false">
    <DialogContent>

      <DialogHeader>
        <DialogTitle>更多</DialogTitle>
        <DialogDescription>
          更多信息
        </DialogDescription>
      </DialogHeader>
      <div>
        每个任务的输入、代码、结果、图表和论文保存在独立工作区：
        <code>E:\MathModelAgentPi\workspaces\&lt;task-id&gt;</code>
      </div>

      <div>
        模型、凭据和会话由本机 Pi 配置管理，浏览器不会保存 API Key。
      </div>

    </DialogContent>
  </Dialog>
</template>
