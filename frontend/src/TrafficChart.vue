<script setup>
import { computed } from 'vue';
const props = defineProps({history:{type:Array,default:()=>[]}});
const points=computed(()=>props.history||[]);
const rate=n=>n==null?'—':n>=1e6?(n/1e6).toFixed(1)+' Mbps':n>=1e3?(n/1e3).toFixed(1)+' Kbps':Math.round(n)+' bps';
const time=at=>new Date(at*1000).toLocaleTimeString('zh-CN',{hour12:false});
const peak=computed(()=>Math.max(0,...points.value.flatMap(p=>[p.upload_bps||0,p.download_bps||0])));
const ceiling=computed(()=>Math.max(1000,peak.value)*1.1);
const x=p=>points.value.length<2?250:12+(p.at-points.value[0].at)/Math.max(1,points.value.at(-1).at-points.value[0].at)*476;
const y=n=>82-Math.max(0,n)/ceiling.value*68;
function path(key){let d='',previous;for(const p of points.value){if(p.state==='offline'||p[key]==null){previous=null;continue;}d+=`${previous&&p.at-previous.at<=180?'L':'M'}${x(p).toFixed(1)},${y(p[key]).toFixed(1)} `;previous=p;}return d;}
function offlinePath(){let d='',previous;for(const p of points.value){if(p.state!=='offline'){previous=null;continue;}d+=`${previous&&p.at-previous.at<=180?'L':'M'}${x(p).toFixed(1)},82 `;previous=p;}return d;}
</script>
<template>
 <div class="history traffic-chart">
  <div class="traffic-heading"><div class="traffic-legend"><span class="traffic-up">上传</span><span class="traffic-down">下载</span></div><small>实时流量</small></div>
  <svg v-if="points.length" viewBox="0 0 500 98" preserveAspectRatio="none" role="img" aria-label="最近两小时上传和下载速率曲线">
   <path class="traffic-grid" d="M12 14H488 M12 48H488 M12 82H488"/>
   <path class="traffic-line traffic-offline" :d="offlinePath()"/><path class="traffic-line traffic-up" :d="path('upload_bps')"/><path class="traffic-line traffic-down" :d="path('download_bps')"/>
   <template v-for="p in points" :key="p.at"><circle v-if="p.state==='offline'" class="traffic-offline" :cx="x(p)" cy="82" r="1.6"/><circle v-if="p.state!=='offline'&&p.upload_bps!=null" class="traffic-up" :cx="x(p)" :cy="y(p.upload_bps)" r="1.6"/><circle v-if="p.state!=='offline'&&p.download_bps!=null" class="traffic-down" :cx="x(p)" :cy="y(p.download_bps)" r="1.6"/><rect :x="x(p)-Math.max(2,238/points.length)" y="0" :width="Math.max(4,476/points.length)" height="98" fill="transparent"><title>{{time(p.at)}} · {{p.state==='offline'?'离线':`上传 ${rate(p.upload_bps)} · 下载 ${rate(p.download_bps)}`}}</title></rect></template>
  </svg>
  <div v-else class="traffic-empty">等待流量采集</div>
  <div class="traffic-footer"><span>{{points.length?time(points[0].at):'最近两小时'}}</span><span v-if="points.length">峰值 {{rate(peak)}}</span><span>{{points.length?time(points.at(-1).at):'—'}}</span></div>
 </div>
</template>
