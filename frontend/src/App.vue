<script setup lang="ts">
import Toaster from "@/components/ui/toast/Toaster.vue";
</script>

<template>
  <Toaster />
  <router-view :key="$route.path" />
</template>