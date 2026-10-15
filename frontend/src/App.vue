<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import HeartbeatBar from "./HeartbeatBar.vue";
import { createFaviconBadge } from "./faviconBadge.js";
const admin = location.pathname === "/admin";
const authenticated = ref(false), loading = ref(true), busy = ref(false), failure = ref(""), error = ref(""), notice = ref("");
const data = ref({routers:[],devices:[],totals:{}});
const loginForm = ref({username:"",password:""});
const tab = ref("devices"), selectedId = ref(null), query = ref(""), onlyOnline = ref(true);
const labels = {healthy:"在线",offline:"离线",unknown:"未知",pending:"等待采集"};
const routerBlank = () => ({name:"",address:"",brand:"zte",username:"admin",password:""});
const routerForm = ref(routerBlank());
const deviceForm = ref({name:"",group:"",notes:"",visibility:"auto",notify_offline:false});
const selected = computed(() => (tab.value === "routers" ? data.value.routers : data.value.devices).find(d=>d.id===selectedId.value));
const filtered = computed(() => {
 const list = tab.value === "routers" ? data.value.routers : data.value.devices;
 const needle = query.value.trim().toLowerCase();
 return list.filter(d=>(tab.value!=="devices"||!onlyOnline.value||d.state==="healthy")&&(!needle||[d.name,d.ip,d.mac,d.brand,d.group].some(v=>String(v||"").toLowerCase().includes(needle))));
});
const groups = computed(() => {
 const map = new Map();
 for (const d of data.value.devices) { const name=d.group||"家庭设备";if(!map.has(name))map.set(name,[]);map.get(name).push(d); }
 return [...map].map(([name,devices])=>({name,devices}));
});
const attention = computed(()=>data.value.routers.filter(r=>(r.state!=="healthy"||r.wan_state==="offline")).length+data.value.devices.filter(d=>d.notify_offline&&d.state==="offline").length);
const badge = createFaviconBadge();
watch(attention,n=>badge.update(n));
let requested=0, applied=0, timer;
async function api(path,method="GET",body) {
 const r = await fetch(path,{method,headers:{"Content-Type":"application/json","X-HomeLens-Request":"1"},body:body===undefined?undefined:JSON.stringify(body)});
 const b = await r.json();
 if(r.status===401 && path!=="/api/login")authenticated.value=false;
 if(!r.ok)throw Error(b.error||"请求失败");return b;
}
async function refresh(){
 const seq=++requested;
 try{const b=await api(admin&&authenticated.value?"/api/admin":"/api/status");if(seq<applied)return;applied=seq;data.value=b;failure.value="";}
 catch(e){if(seq>=applied){applied=seq;failure.value=e.message;}}
 finally{loading.value=false;}
}
function select(d){
 selectedId.value=d?.id||null;error.value="";
 if(tab.value==="routers")routerForm.value=d?{...routerBlank(),...d.config,name:d.custom_name||"",password:""}:routerBlank();
 else if(d)deviceForm.value={name:d.custom_name||"",group:d.custom_group||"",notes:d.notes||"",visibility:d.visibility,notify_offline:d.notify_offline};
}
const previousSelection = {devices:null,routers:null};
function switchTab(value){
 previousSelection[tab.value]=selectedId.value;
 tab.value=value;query.value="";
 select(filtered.value.find(d=>d.id===previousSelection[value])||filtered.value[0]||null);
}
async function login(){busy.value=true;error.value="";try{await api('/api/login','POST',loginForm.value);loginForm.value.password="";authenticated.value=true;await refresh();if(!data.value.routers.length)tab.value='routers';select(filtered.value[0]);}catch(e){error.value=e.message;}finally{busy.value=false;}}
async function logout(){await api('/api/logout','POST',{});authenticated.value=false;selectedId.value=null;routerForm.value=routerBlank();}
async function save(){
 busy.value=true;error.value="";
 try{
  if(tab.value==='routers'){
   const body={...routerForm.value};const result=await api('/api/routers'+(selectedId.value?'/'+selectedId.value:''),selectedId.value?'PATCH':'POST',body);
   if(result.id)selectedId.value=result.id;routerForm.value.password="";
  }else await api('/api/devices/'+selectedId.value,'PATCH',deviceForm.value);
  await refresh();notice.value="已保存";setTimeout(()=>notice.value="",2500);
 }catch(e){error.value=e.message;}finally{busy.value=false;}
}
const rate=n=>n==null?'—':n>=1e9?(n/1e9).toFixed(1)+' Gbps':n>=1e6?(n/1e6).toFixed(1)+' Mbps':n>=1e3?(n/1e3).toFixed(1)+' Kbps':Math.round(n)+' bps';
const bytes=n=>n==null?'—':n>=1024**3?(n/1024**3).toFixed(1)+' GB':n>=1024**2?(n/1024**2).toFixed(1)+' MB':n>=1024?(n/1024).toFixed(1)+' KB':n+' B';
const time=at=>at?new Date(at*1000).toLocaleString('zh-CN',{hour12:false}):'等待采集';
function visibleRefresh(){if(!document.hidden)refresh();}
onMounted(async()=>{if(admin){try{authenticated.value=(await api('/api/auth')).authenticated;}catch{}}
 await refresh();if(admin&&authenticated.value){if(!data.value.routers.length)tab.value='routers';select(filtered.value[0]);}
 timer=setInterval(visibleRefresh,5000);window.addEventListener('focus',visibleRefresh);document.addEventListener('visibilitychange',visibleRefresh);
});
onUnmounted(()=>{clearInterval(timer);badge.dispose();window.removeEventListener('focus',visibleRefresh);document.removeEventListener('visibilitychange',visibleRefresh);});
</script>
<template>
 <main v-if="admin&&!authenticated" class="login panel">
  <img src="/avatar.png" class="avatar" alt=""/><h1>HomeLens 管理</h1>
  <form @submit.prevent="login"><label>账号<input v-model="loginForm.username" autocomplete="username" required/></label><label>密码<input v-model="loginForm.password" type="password" autocomplete="current-password" required/></label><p class="form-error" role="alert">{{error}}</p><button class="btn primary" :disabled="busy">{{busy?'正在登录…':'登录'}}</button></form>
 </main>
 <main v-else class="page" :class="{'admin-page':admin}">
  <div v-if="admin" class="admin-toolbar"><a href="/">← 展示页</a><button class="btn" @click="logout">退出</button></div>
  <header class="page-title"><img src="/avatar.png" class="avatar" alt=""/><h1>{{admin?'咸鱼的家庭管理':'咸鱼的家庭状态'}}</h1></header>
  <p v-if="failure" class="connection-error" role="alert">连接中断，正在展示上次收到的数据。</p>
  <div v-if="admin" class="admin-layout">
   <aside class="device-sidebar panel"><div class="sidebar-inner">
    <div class="home-tabs" role="group" aria-label="管理类型"><button class="btn" :class="{primary:tab==='devices'}" @click="switchTab('devices')">设备管理</button><button class="btn" :class="{primary:tab==='routers'}" @click="switchTab('routers')">路由器接入</button></div>
    <input v-model="query" class="home-search" placeholder="搜索名称、IP、MAC" aria-label="搜索设备"/>
    <label v-if="tab==='devices'" class="home-check"><input v-model="onlyOnline" type="checkbox"/>只看在线设备</label>
    <button v-if="tab==='routers'" class="btn add-device" @click="select(null)">＋ 添加路由器</button>
    <div class="sidebar-scroll"><button v-for="d in filtered" :key="d.id" class="sidebar-device" :class="[d.state,{selected:selectedId===d.id}]" @click="select(d)"><span class="status-dot"></span><span class="sidebar-name">{{d.name}}<small>{{labels[d.state]}}<template v-if="d.ip"> · {{d.ip}}</template></small></span></button><p v-if="!filtered.length" class="hint">{{loading?'正在获取…':'暂无匹配的设备'}}</p></div>
   </div></aside>
   <section v-if="tab==='routers'" class="editor panel">
    <div class="editor-heading"><h2>{{selected?'路由器接入':'添加路由器'}}</h2><span v-if="selected" class="state" :class="selected.state">{{labels[selected.state]}}</span></div>
    <form @submit.prevent="save"><h3>连接信息</h3><div class="form-grid"><label>路由器品牌<select v-model="routerForm.brand"><option value="zte">中兴 ZTE</option></select></label><label>管理地址<input v-model="routerForm.address" type="url" placeholder="http://192.168.1.1" required/></label></div>
     <div class="form-grid"><label>管理账号<input v-model="routerForm.username" placeholder="admin" autocomplete="off"/></label><label>管理密码<input v-model="routerForm.password" type="password" autocomplete="new-password" :required="!selected?.has_password" :placeholder="selected?.has_password?'已保存，留空保留原密码':'路由器管理密码'"/></label></div>
     <h3>显示信息</h3><label>自定义名称<input v-model="routerForm.name" placeholder="留空则自动获取型号" maxlength="200"/></label>
     <div v-if="selected" class="home-router-detail"><span>型号 <strong>{{selected.model||'等待识别'}}</strong></span><span>固件 <strong>{{selected.version||'—'}}</strong></span><span>最近采集 <strong>{{time(selected.checked_at)}}</strong></span><p v-if="selected.error" class="form-error">{{selected.error}}</p></div>
     <p class="form-error" role="alert">{{error}}</p><div class="actions"><span></span><button class="btn primary" :disabled="busy">{{busy?'正在保存…':selected?'保存':'接入并发现设备'}}</button></div>
    </form>
   </section>
   <section v-else-if="selected" class="editor panel">
    <div class="editor-heading"><h2>{{selected.name}}</h2><span class="state" :class="selected.state">{{labels[selected.state]}}</span></div>
    <form @submit.prevent="save"><h3>显示信息</h3><div class="form-grid"><label>自定义名称<input v-model="deviceForm.name" :placeholder="selected.automatic_name||'留空自动获取'" maxlength="200"/></label><label>房间 / 分组<input v-model="deviceForm.group" placeholder="家庭设备" maxlength="200"/></label></div><label>备注<input v-model="deviceForm.notes" placeholder="仅在管理后台显示" maxlength="1000"/></label>
     <div class="form-grid"><label>展示方式<select v-model="deviceForm.visibility"><option value="auto">自动 · 仅显示在线设备</option><option value="show">始终显示</option><option value="hide">隐藏</option></select></label><label class="home-check home-notify"><input v-model="deviceForm.notify_offline" type="checkbox"/>离线时提醒</label></div>
     <h3>自动获取的信息</h3><dl class="home-device-detail"><div><dt>IP 地址</dt><dd>{{selected.ip||'—'}}</dd></div><div><dt>MAC 地址</dt><dd>{{selected.mac}}</dd></div><div><dt>设备品牌 / 型号</dt><dd>{{[selected.brand,selected.model].filter(Boolean).join(' · ')||'未识别'}}</dd></div><div><dt>连接方式</dt><dd>{{selected.connection||'—'}}</dd></div><div><dt>信号强度</dt><dd>{{selected.signal!=null?selected.signal+' dBm':'—'}}</dd></div><div><dt>接入节点</dt><dd>{{selected.ap||selected.router_name}}</dd></div><div><dt>累计上传</dt><dd>{{bytes(selected.sent_bytes)}}</dd></div><div><dt>累计下载</dt><dd>{{bytes(selected.received_bytes)}}</dd></div></dl>
     <p class="form-error" role="alert">{{error}}</p><div class="actions"><span></span><button class="btn primary" :disabled="busy">{{busy?'正在保存…':'保存'}}</button></div>
    </form>
   </section><section v-else class="empty panel"><p>选择设备以修改名称、分组和展示方式。</p></section>
  </div>
  <template v-else>
   <section class="overall panel" :class="attention?'slow':'healthy'"><span class="overall-icon">{{attention?'!':'✓'}}</span><h2>{{loading?'正在获取家庭状态…':!data.routers.length?'尚未接入路由器':attention?`${attention} 项家庭网络状态需要关注`:'家庭网络运行正常'}}</h2></section>
   <section v-if="!loading&&!data.routers.length" class="empty panel"><p>接入路由器后自动发现家庭设备。</p><a href="/admin">前往管理后台</a></section>
   <section v-for="r in data.routers" :key="r.id" class="home-network panel" :class="r.state"><div class="device-name-row"><span class="status-dot"></span><strong>{{r.name}}</strong><span class="tag">{{r.model||'等待识别'}}</span><small class="home-state">{{r.state!=='healthy'?labels[r.state]:r.wan_state==='offline'?'外网不可用':'外网正常'}}</small><small v-if="r.error" class="home-error">{{r.error}}</small></div>
    <div class="home-network-values"><div><small>在线设备</small><strong>{{r.state==='healthy'?r.online_count:'—'}}<em> 台</em></strong></div><div><small>网络节点</small><strong>{{r.nodes?.length??'—'}}<em> 个</em></strong></div><div><small>当前上传</small><strong>{{r.state==='healthy'?rate(r.upload_bps):'—'}}</strong></div><div><small>当前下载</small><strong>{{r.state==='healthy'?rate(r.download_bps):'—'}}</strong></div></div>
   </section>
   <section v-for="g in groups" :key="g.name" class="group"><h2>{{g.name}}</h2><div class="device-list panel"><article v-for="d in g.devices" :key="d.id" class="device home-device" :class="d.state">
    <div class="home-summary"><div class="device-name-row"><span class="status-dot"></span><strong class="device-name">{{d.name}}</strong><span class="tag">{{d.connection}}</span><small class="home-state">{{labels[d.state]}}</small></div><div class="home-values"><div><small>IP 地址</small><strong>{{d.ip||'—'}}</strong></div><div><small>MAC 地址</small><strong class="home-mac">{{d.mac}}</strong></div><div><small>信号</small><strong>{{d.signal!=null?d.signal+' dBm':d.connection==='有线'?'有线连接':'—'}}</strong></div><div><small>当前上传</small><strong>{{d.state==='healthy'?rate(d.upload_bps):'—'}}</strong></div><div><small>当前下载</small><strong>{{d.state==='healthy'?rate(d.download_bps):'—'}}</strong></div><div><small>接入节点</small><strong>{{d.ap||d.router_name}}</strong></div></div><p class="last-seen">{{[d.brand,d.model].filter(Boolean).join(' · ')}}<span v-if="d.brand||d.model"> · </span>更新于 {{time(d.updated_at)}}</p></div>
    <HeartbeatBar :history="d.history"/>
   </article></div></section>
  </template>
 </main><div v-if="notice" class="notice" role="status">{{notice}}</div>
</template>
