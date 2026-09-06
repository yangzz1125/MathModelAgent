<script setup lang="ts">
import {
	getAllFilesDownloadUrl,
	getDeliveryPackageUrl,
	getFileDownloadUrl,
	getFiles,
} from "@/apis/filesApi";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast/use-toast";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	Archive,
	Download,
	File,
	FileText,
	Files,
	PackageCheck,
	RefreshCw,
} from "lucide-vue-next";
import { ref } from "vue";
import { useRoute } from "vue-router";

// ---- Reactive State ----

const route = useRoute();
const taskId = route.params.task_id;
const { toast } = useToast();

/** 工作区文件 */
interface WorkspaceFile {
	filename: string;
	file_type: string;
	size?: number;
	modified_time?: string;
}

/** 文件列表弹窗显示状态 */
const fileListVisible = ref(false);

/** 文件列表数据 */
const fileList = ref<WorkspaceFile[]>([]);

/** 加载状态 */
const loadingFiles = ref(false);

/** 当前正在下载的文件名 */
const downloadingFile = ref<string | null>(null);

/** 是否正在下载全部文件 */
const downloadingAll = ref(false);

/** 是否正在生成正式交付包 */
const packaging = ref(false);

// ---- Methods ----

/** 打开文件列表弹窗 */
const openFolder = async () => {
	try {
		loadingFiles.value = true;
		const res = await getFiles(taskId as string);

		if (res.data) {
			fileList.value = res.data;
			fileListVisible.value = true;
		} else {
			toast({
				title: "获取文件列表失败",
				description: "无法获取工作区文件列表",
				variant: "destructive",
			});
		}
	} catch (error) {
		console.error("获取文件列表失败:", error);
		toast({
			title: "错误",
			description: "获取文件列表时出现错误",
			variant: "destructive",
		});
	} finally {
		loadingFiles.value = false;
	}
};

/** 根据文件名获取对应的图标组件 */
const getFileIcon = (fileName: string) => {
	const ext = fileName.split(".").pop()?.toLowerCase();
	const textExts = ["txt", "md", "json", "csv", "xml", "yml", "yaml"];

	if (textExts.includes(ext || "")) {
		return FileText;
	}
	return File;
};

/** 格式化文件大小显示 */
const formatFileSize = (size: number | undefined) => {
	if (!size) return "";

	const units = ["B", "KB", "MB", "GB"];
	let unitIndex = 0;
	let fileSize = size;

	while (fileSize >= 1024 && unitIndex < units.length - 1) {
		fileSize /= 1024;
		unitIndex++;
	}

	return `${fileSize.toFixed(1)} ${units[unitIndex]}`;
};

/** 下载单个文件 */
const downloadSingleFile = async (filename: string) => {
	try {
		downloadingFile.value = filename;
		const res = await getFileDownloadUrl(taskId as string, filename);
		if (res.data?.download_url) {
			// 创建隐藏的链接元素并触发下载
			const link = document.createElement("a");
			link.href = res.data.download_url;
			link.download = filename;
			link.target = "_blank";
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);

			toast({
				title: "下载成功",
				description: `文件 ${filename} 开始下载`,
			});
		} else {
			throw new Error("获取下载链接失败");
		}
	} catch (error) {
		console.error("下载文件失败:", error);
		toast({
			title: "下载失败",
			description: `下载文件 ${filename} 时出现错误`,
			variant: "destructive",
		});
	} finally {
		downloadingFile.value = null;
	}
};

/** 下载所有文件（压缩包） */
const downloadAll = async () => {
	try {
		downloadingAll.value = true;
		const res = await getAllFilesDownloadUrl(taskId as string);
		if (res.data?.download_url) {
			// 创建隐藏的链接元素并触发下载
			const link = document.createElement("a");
			link.href = res.data.download_url;
			link.download = `task_${taskId}_files.zip`;
			link.target = "_blank";
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);

			toast({
				title: "下载成功",
				description: "所有文件压缩包开始下载",
			});
		} else {
			throw new Error("获取下载链接失败");
		}
	} catch (error) {
		console.error("下载所有文件失败:", error);
		toast({
			title: "下载失败",
			description: "下载所有文件时出现错误",
			variant: "destructive",
		});
	} finally {
		downloadingAll.value = false;
	}
};

/** 生成并下载经过验收的正式交付包 */
const downloadPackage = async () => {
	try {
		packaging.value = true;
		const res = await getDeliveryPackageUrl(taskId as string);
		if (!res.data?.download_url) throw new Error("获取交付包链接失败");
		const link = document.createElement("a");
		link.href = res.data.download_url;
		link.download = `MathModelAgent-${taskId}-delivery.zip`;
		link.target = "_blank";
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);
		toast({
			title: "开始生成交付包",
			description: "验收通过后将下载 PDF、源码、代码、数据和图表",
		});
	} catch (error) {
		console.error("生成交付包失败:", error);
		toast({
			title: "打包失败",
			description: "任务尚未完成验收或交付文件不完整",
			variant: "destructive",
		});
	} finally {
		packaging.value = false;
	}
};
</script>

<template>
  <Sheet v-model:open="fileListVisible">
    <SheetTrigger asChild>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger as-child>
            <Button @click="openFolder()" :disabled="loadingFiles" class="flex gap-2" size="icon">
              <RefreshCw v-if="loadingFiles" class="w-4 h-4 animate-spin" />
              <Files v-else class="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>工作区文件</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

    </SheetTrigger>
    <SheetContent side="right" class="w-[400px] sm:w-[540px]">
      <SheetHeader>
        <SheetTitle class="flex items-center justify-between mr-5">
          <span>工作区文件</span>
          <div class="flex items-center gap-1">
            <Button size="icon" variant="ghost" :disabled="packaging" @click="downloadPackage" title="下载正式交付包">
              <RefreshCw v-if="packaging" class="h-4 w-4 animate-spin" />
              <PackageCheck v-else class="h-4 w-4" />
            </Button>
            <Button size="icon" variant="ghost" :disabled="downloadingAll" @click="downloadAll" title="下载全部工作区文件">
              <RefreshCw v-if="downloadingAll" class="h-4 w-4 animate-spin" />
              <Archive v-else class="h-4 w-4" />
            </Button>
          </div>
        </SheetTitle>
        <SheetDescription>
          任务产物保存在 <span class="font-mono">E:\MathModelAgentPi\workspaces\{{ taskId }}</span>
        </SheetDescription>
      </SheetHeader>

      <div class="mt-6">
        <ScrollArea class="h-[calc(100vh-120px)]">
          <div v-if="fileList.length === 0" class="text-center py-8 text-gray-500">
            暂无文件
          </div>
          <div v-else class="space-y-2">
            <div v-for="(file, index) in fileList" :key="index"
              class="flex items-center gap-3 p-3 rounded-lg border hover:bg-gray-50 transition-colors">
              <component :is="getFileIcon(file.filename)"
                class="w-5 h-5 text-gray-600 flex-shrink-0" />
              <div class="flex-1 min-w-0">
                <div class="font-medium text-sm truncate">
                  {{ file.filename }}
                </div>
                <div class="text-xs text-gray-500 flex gap-2">
                  <span v-if="file.size">{{ formatFileSize(file.size) }}</span>
                  <span v-if="file.modified_time">{{ new Date(file.modified_time).toLocaleDateString()
                    }}</span>
                  <span>{{ file.file_type }}</span>
                </div>
              </div>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger as-child>
                    <Button @click="downloadSingleFile(file.filename)"
                      :disabled="downloadingFile === file.filename" size="sm" variant="ghost"
                      class="flex-shrink-0">
                      <RefreshCw v-if="downloadingFile === file.filename"
                        class="w-4 h-4 animate-spin" />
                      <Download v-else class="w-4 h-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>下载文件</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
          </div>
        </ScrollArea>

      </div>

    </SheetContent>
  </Sheet>
</template>
