<script setup>
defineProps({ history: { type: Array, default: () => [] } });
const labels = { healthy: "在线", offline: "离线", unknown: "未知" };
const time = at => new Date(at * 1000).toLocaleString("zh-CN", { hour12: false });
</script>
<template>
  <div class="history">
    <div class="beats" aria-label="最近五小时的实际设备状态记录">
      <span v-for="(h,i) in history" :key="h.at" class="beat" :class="h.state" :style="i===0?{gridColumn:61-history.length}:undefined" :title="`${time(h.at)} · ${labels[h.state] || h.state}`"></span>
    </div>
    <div class="beat-time"><span>{{ history.length ? new Date(history[0].at*1000).toLocaleTimeString('zh-CN',{hour12:false}) : '暂无检测记录' }}</span><span>最近检测</span></div>
  </div>
</template>
