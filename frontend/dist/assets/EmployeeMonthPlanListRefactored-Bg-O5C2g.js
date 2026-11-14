import{E as Mt}from"./el-alert-24frMtzj.js";import{g as gt,o as m,j as w,F as K,k as t,x as i,E as _t,l as h,a1 as Nt,y as u,z as v,G as j,aK as Vt,m as A,aL as Pt,t as o,r as bt,a9 as Tt,s as M,Z as St,f as T,ah as R,K as $t,aa as At,L as Rt,M as jt,aC as ct,ag as ft,a7 as Dt,n as vt,aj as Lt}from"./index-DNCFA6gS.js";import{E as Bt}from"./el-tag-DII5NNqC.js";import{E as Ft}from"./el-date-picker-DovhLRWl.js";import{a as qt,E as zt}from"./el-table-column-C-Slb7YQ.js";import{v as It}from"./el-loading-CuYS8Nfq.js";import"./el-tooltip-l0sNRNKZ.js";/* empty css                    */import{l as Ut}from"./planning-lTMAuKJi.js";import{t as Ot,u as Ht,v as Wt,a as yt,x as ht,w as Yt}from"./employee-387bE6M0.js";import{c as ut}from"./performance-ImcHE3HC.js";import{u as Gt}from"./usePersonalMonthPlanRoutes-B91DU4JU.js";import{a as Kt,E as Jt}from"./el-form-item-CAB7JT0P.js";import{a as Zt,E as Qt}from"./el-radio-button-H6tSo8a-.js";import{E as Xt}from"./el-dialog-DFUsXRqe.js";import{E as te}from"./el-drawer-B2f1BcuL.js";import{E as ee}from"./el-empty-CsMV54jS.js";import{E as ae}from"./el-skeleton-BvzJDrMc.js";import{s as le}from"./download-Bfs36T7L.js";import{M as wt}from"./MonthPlanTaskContentView-CMct5eWG.js";import{a as kt,f as dt}from"./monthPlanPeriod-C1u--jm3.js";import{_ as ne}from"./_plugin-vue_export-helper-DlAUqK2U.js";import{P as se,a as oe}from"./PlanAiReviewDrawer-CbWdOBmh.js";import{u as ie}from"./useAiReviewCollection-CL3NGYg_.js";import"./vnode-BF6iCwKh.js";import"./index-BLzbBYyW.js";import"./index-BNwinwYD.js";import"./debounce-PWkStjeX.js";import"./omit-kB6hKyav.js";import"./_baseClone-BrFVUfhc.js";import"./_baseIteratee-Cl1tgnNs.js";import"./index-CFVp4-TC.js";import"./use-dialog-547Rqlwz.js";import"./AiReviewPanel-gUzrAzEu.js";import"./aiReview-CRkQ0oMi.js";const re={class:"employee-month-support-actions"},ue={class:"template-actions"},de={key:1,class:"template-preview"},pe={class:"template-preview__meta"},me={class:"template-preview__table-wrap"},ce={class:"template-standard-cell"},fe=gt({__name:"EmployeeMonthPlanSupportActions",props:{detail:{}},setup(J){const E=J,L=M(!1),k=M(!1),q=M(!1),f=M(null),N=M(!1),B=M(!1),d=St({adjustmentType:"PAUSE",reason:"",impactText:""}),U=T(()=>E.detail.status==="submitted"||E.detail.status==="approved"),s=T(()=>{var n;return(n=f.value)!=null&&n.items.length?f.value.items:Array.from({length:8},(l,y)=>({rowNo:y+1,taskContent:"",performanceWeight:0,deliverable:"",quality:"",estimatedStartDate:"",estimatedEndDate:"",estimatedValue:0}))});async function F(){L.value=!0,await S()}async function S(){k.value=!0;try{f.value=await Ot(E.detail.id)}catch(n){f.value=null,R.error(n instanceof Error?n.message:"模板预览加载失败")}finally{k.value=!1}}async function O(){q.value=!0;try{const n=await Ht(E.detail.id);le(n,`${E.detail.employeeName||"员工"}-${E.detail.planMonth}-月计划.xlsx`)}catch(n){R.error(n instanceof Error?n.message:"模板下载失败")}finally{q.value=!1}}function lt(){if(!f.value){R.warning("请先加载模板预览");return}const n=window.open("","_blank","width=1120,height=780");if(!n){R.error("浏览器拦截了打印窗口，请允许弹窗后重试");return}n.document.open(),n.document.write($()),n.document.close(),n.focus(),window.setTimeout(()=>{n.print()},180)}async function H(){if(!d.reason.trim()){R.warning("请填写申请原因");return}B.value=!0;try{await Wt({planType:"MONTH",planId:E.detail.id,adjustmentType:d.adjustmentType,reason:d.reason.trim(),impactText:d.impactText.trim()}),N.value=!1,d.reason="",d.impactText="",R.success("调整申请已提交直属领导处理")}catch(n){R.error(n instanceof Error?n.message:"调整申请提交失败")}finally{B.value=!1}}function W(n){const[l,y]=String(n||"").split("-"),C=Number(y);return!l||!Number.isFinite(C)?"        年      月个人目标制定":`${l} 年 ${String(C).padStart(2,"0")} 月个人目标制定`}function Z(n){return`¥${Number(n||0).toLocaleString("zh-CN",{maximumFractionDigits:2})}`}function Q(n){const l=Number(n||0);return Number.isFinite(l)?l.toLocaleString("zh-CN",{maximumFractionDigits:2,useGrouping:!1}):""}function $(){const n=f.value;if(!n)return"";const l=x(W(n.planMonth)),y=s.value.map(g=>`
    <tr class="template-print-item-row">
      <td class="cell-center">${x(g.rowNo)}</td>
      <td>${X(g.taskContent)}</td>
      <td class="cell-center">${g.performanceWeight||""}</td>
      <td>${D(g.deliverable,g.quality)}</td>
      <td class="cell-center">${x(dt(g.estimatedStartDate,g.estimatedEndDate,""))}</td>
      <td class="cell-center">${g.estimatedValue?x(Q(g.estimatedValue)):""}</td>
    </tr>
  `).join(""),C=n.estimatedValueTotal?x(Q(n.estimatedValueTotal)):"";return`<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>${l}</title>
  <style>
    @page { size: A4 landscape; margin: 0; }
    * { box-sizing: border-box; }
    html, body { width: 297mm; min-height: 210mm; margin: 0; padding: 0; background: #fff; }
    body {
      color: #000;
      font-family: "SimSun", "Microsoft YaHei", Arial, sans-serif;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }
    .sheet { width: 297mm; min-height: 210mm; margin: 0 auto; padding: 9mm 12mm; background: #fff; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; color: #000; font-size: 8.5px; line-height: 1.32; }
    col:nth-child(1) { width: 5.5%; }
    col:nth-child(2) { width: 29.4%; }
    col:nth-child(3) { width: 4.9%; }
    col:nth-child(4) { width: 35.2%; }
    col:nth-child(5) { width: 13.8%; }
    col:nth-child(6) { width: 11.2%; }
    th, td { padding: 3px 4px; border: 1px solid #000; vertical-align: middle; word-break: break-word; overflow-wrap: anywhere; }
    th { background: #fff; font-weight: 700; text-align: center; }
    .template-print-title-row td {
      height: 25px;
      padding: 0 4px 4px;
      border: 0;
      font-size: 18px;
      font-weight: 700;
      line-height: 1.2;
      text-align: center;
    }
    .template-print-meta-row td {
      height: 24px;
      padding: 0 0 5px;
      border: 0;
      font-size: 10px;
      line-height: 1.2;
    }
    .template-print-meta {
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 28px;
      width: 100%;
      white-space: nowrap;
    }
    .template-print-column-row th { height: 30px; }
    .template-print-item-row td { vertical-align: top; }
    .template-print-item-row td.cell-center, .cell-center { text-align: center; vertical-align: middle !important; }
    .task-title, .standard-label { font-weight: 700; }
    .task-title { display: block; margin-bottom: 2px; }
    .line { display: block; }
    .template-print-total-row td { height: 19px; font-weight: 700; text-align: center; vertical-align: middle; }
    .template-print-confirm-row td {
      height: 62px;
      padding: 7px 8px;
      font-size: 10px;
      line-height: 1.6;
      vertical-align: top;
    }
    .template-print-confirm-row p { margin: 0 0 7px; }
    .template-print-signature-line {
      display: flex;
      align-items: end;
      margin-top: 3px;
      white-space: nowrap;
    }
    .template-print-signature-label { width: 120px; }
    .template-print-signature-blank { display: inline-block; width: 210px; min-height: 1em; border-bottom: 1px solid #000; }
    .template-print-signature-date { margin-left: 285px; }
    tr { break-inside: avoid; page-break-inside: avoid; }
  </style>
</head>
<body>
  <main class="sheet">
    <table>
      <colgroup><col><col><col><col><col><col></colgroup>
      <tbody>
        <tr class="template-print-title-row">
          <td colspan="6">${l}</td>
        </tr>
        <tr class="template-print-meta-row">
          <td colspan="6">
            <div class="template-print-meta">
              <span>姓名：${x(n.employeeName||E.detail.employeeName||"")}</span>
              <span>项目：${x(n.projectName||"")}</span>
              <span>岗位：${x(n.positionName||E.detail.positionName||"")}</span>
            </div>
          </td>
        </tr>
        <tr class="template-print-column-row">
          <th>序号</th>
          <th>工作计划内容</th>
          <th>权重（%）</th>
          <th>考核标准（交付物、质量）</th>
          <th>完成时间段（从开始到结束时间）</th>
          <th>预估价值</th>
        </tr>
        ${y}
        <tr class="template-print-total-row">
          <td colspan="5">预估价值合计</td>
          <td>${C}</td>
        </tr>
        <tr class="template-print-confirm-row">
          <td colspan="6">
            <p>本人核对表格内月度工作计划目标，确认信息真实完整无误，知晓并认可表格兼具目标确定和后续绩效考核有关，对以上内容无异议。</p>
            <div class="template-print-signature-line"><span class="template-print-signature-label">员工本人签字捺印：</span><span class="template-print-signature-blank"></span><span class="template-print-signature-date">时间：____年__月__日</span></div>
            <div class="template-print-signature-line"><span class="template-print-signature-label">项目经理签字：</span><span class="template-print-signature-blank"></span><span class="template-print-signature-date">时间：____年__月__日</span></div>
            <div class="template-print-signature-line"><span class="template-print-signature-label">部门负责人签字：</span><span class="template-print-signature-blank"></span><span class="template-print-signature-date">时间：____年__月__日</span></div>
          </td>
        </tr>
      </tbody>
    </table>
  </main>
</body>
</html>`}function X(n){const l=nt(n);if(!l.length)return"";if(l.length===1)return x(l[0]);const[y,...C]=l;return`<span class="task-title">${x(y)}</span>${C.map(g=>`<span class="line">${x(g)}</span>`).join("")}`}function D(n,l){const y=String(n||"").trim(),C=String(l||"").trim();return[y?`<span class="standard-label">交付物：</span>${x(y)}`:"",C?`<span class="standard-label">质量：</span>${x(C)}`:""].filter(Boolean).map(g=>`<span class="line">${g}</span>`).join("")}function nt(n){return String(n||"").replace(/\r\n?/g,`
`).split(`
`).map(l=>l.trim()).filter(Boolean)}function x(n){return String(n??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;")}return(n,l)=>{const y=_t,C=ae,g=ee,Y=te,tt=Qt,st=Zt,z=Jt,I=Tt,ot=Kt,et=Xt;return m(),w(K,null,[t("div",re,[i(y,{plain:"",icon:h(Nt),onClick:F},{default:u(()=>[...l[7]||(l[7]=[v("模板预览",-1)])]),_:1},8,["icon"]),U.value?(m(),j(y,{key:0,plain:"",type:"warning",icon:h(Vt),onClick:l[0]||(l[0]=p=>N.value=!0)},{default:u(()=>[...l[8]||(l[8]=[v("申请暂停或撤销",-1)])]),_:1},8,["icon"])):A("",!0)]),i(Y,{modelValue:L.value,"onUpdate:modelValue":l[1]||(l[1]=p=>L.value=p),title:"月计划模板预览",size:"min(960px, 96vw)"},{default:u(()=>[t("div",ue,[i(y,{loading:k.value,onClick:S},{default:u(()=>[...l[9]||(l[9]=[v("刷新预览",-1)])]),_:1},8,["loading"]),i(y,{type:"primary",loading:q.value,onClick:O},{default:u(()=>[...l[10]||(l[10]=[v("下载 Excel",-1)])]),_:1},8,["loading"]),i(y,{icon:h(Pt),disabled:k.value||!f.value,onClick:lt},{default:u(()=>[...l[11]||(l[11]=[v("打印",-1)])]),_:1},8,["icon","disabled"])]),k.value?(m(),j(C,{key:0,rows:8,animated:""})):f.value?(m(),w("div",de,[t("h2",null,o(W(f.value.planMonth)),1),t("div",pe,[t("span",null,"姓名："+o(f.value.employeeName||J.detail.employeeName),1),t("span",null,"项目："+o(f.value.projectName||""),1),t("span",null,"岗位："+o(f.value.positionName||J.detail.positionName||""),1)]),t("div",me,[t("table",null,[l[13]||(l[13]=t("thead",null,[t("tr",null,[t("th",null,"序号"),t("th",null,"工作计划内容"),t("th",null,"权重（%）"),t("th",null,"考核标准（交付物、质量）"),t("th",null,"完成时间段"),t("th",null,"预估价值")])],-1)),t("tbody",null,[(m(!0),w(K,null,bt(s.value,p=>(m(),w("tr",{key:p.rowNo},[t("td",null,o(p.rowNo),1),t("td",null,[i(wt,{content:p.taskContent,"empty-text":""},null,8,["content"])]),t("td",null,o(p.performanceWeight||""),1),t("td",ce,o(h(kt)(p.deliverable,p.quality,"")),1),t("td",null,o(h(dt)(p.estimatedStartDate,p.estimatedEndDate,"")),1),t("td",null,o(p.estimatedValue?Z(p.estimatedValue):""),1)]))),128)),t("tr",null,[l[12]||(l[12]=t("td",{colspan:"5"},"预估价值合计",-1)),t("td",null,o(f.value.estimatedValueTotal?Z(f.value.estimatedValueTotal):""),1)])])])])])):(m(),j(g,{key:2,description:"暂无模板预览"}))]),_:1},8,["modelValue"]),i(et,{modelValue:N.value,"onUpdate:modelValue":l[6]||(l[6]=p=>N.value=p),title:"申请暂停或撤销月计划",width:"min(560px, calc(100vw - 32px))"},{footer:u(()=>[i(y,{onClick:l[5]||(l[5]=p=>N.value=!1)},{default:u(()=>[...l[16]||(l[16]=[v("取消",-1)])]),_:1}),i(y,{type:"primary",loading:B.value,onClick:H},{default:u(()=>[...l[17]||(l[17]=[v("提交申请",-1)])]),_:1},8,["loading"])]),default:u(()=>[i(ot,{"label-position":"top"},{default:u(()=>[i(z,{label:"处理类型"},{default:u(()=>[i(st,{modelValue:d.adjustmentType,"onUpdate:modelValue":l[2]||(l[2]=p=>d.adjustmentType=p)},{default:u(()=>[i(tt,{value:"PAUSE"},{default:u(()=>[...l[14]||(l[14]=[v("暂停",-1)])]),_:1}),i(tt,{value:"CANCEL"},{default:u(()=>[...l[15]||(l[15]=[v("撤销",-1)])]),_:1})]),_:1},8,["modelValue"])]),_:1}),i(z,{label:"申请原因"},{default:u(()=>[i(I,{modelValue:d.reason,"onUpdate:modelValue":l[3]||(l[3]=p=>d.reason=p),type:"textarea",rows:4,maxlength:"1000","show-word-limit":""},null,8,["modelValue"])]),_:1}),i(z,{label:"影响说明"},{default:u(()=>[i(I,{modelValue:d.impactText,"onUpdate:modelValue":l[4]||(l[4]=p=>d.impactText=p),type:"textarea",rows:3,maxlength:"1000","show-word-limit":""},null,8,["modelValue"])]),_:1})]),_:1})]),_:1},8,["modelValue"])],64)}}}),ve=ne(fe,[["__scopeId","data-v-45e2adc5"]]),ye={class:"page-panel employee-month-page"},he={key:0,class:"page-header"},ge={class:"page-title"},_e={class:"toolbar"},be={class:"employee-month-browser mt16"},we={class:"employee-month-browser__head"},ke={class:"toolbar"},xe={class:"employee-month-browser__list","aria-label":"月份导航"},Ee=["aria-pressed","onClick"],Ce={class:"employee-month-card__label"},Me={class:"employee-month-card__year"},Ne={class:"employee-month-card__meta"},Ve={class:"employee-month-workspace mt16"},Pe={class:"employee-month-workspace__head"},Te={class:"employee-month-workspace__identity"},Se={class:"employee-month-workspace__summary-meta"},$e={class:"employee-month-workspace__status"},Ae={class:"employee-month-workspace__hint"},Re={class:"employee-month-workspace__actions"},je={class:"employee-month-workspace__action-buttons"},De={class:"employee-month-workspace__metrics"},Le={key:0},Be={key:1},Fe={class:"status-value"},qe={class:"employee-month-tasks"},ze={class:"employee-month-tasks__head"},Ie={key:0,class:"employee-month-readonly-table-wrap"},Ue={class:"readonly-task-index"},Oe={class:"readonly-task-number"},He={class:"readonly-task-text readonly-task-standard"},We={class:"readonly-task-period"},Ye={class:"readonly-task-number"},Ge={class:"readonly-task-result"},Ke={key:1,class:"employee-month-workspace__empty compact"},Je={key:1,class:"employee-month-workspace__empty"},$a=gt({__name:"EmployeeMonthPlanListRefactored",setup(J){const E=$t(),L=At(),{isLeaderContext:k,editPath:q}=Gt(),f=M(!1),N=M(""),B=Array.isArray(L.query.month)?L.query.month[0]:L.query.month,d=M(typeof B=="string"&&/^\d{4}-\d{2}$/.test(B)?B:ut()),U=M([]),s=M(null),F=M([]),S=ie("MONTH_PLAN"),O=M(!1);async function lt(){s.value&&(O.value=!0,await S.loadOne(s.value.id))}const H={draft:{label:"草稿",type:"info"},submitted:{label:"已提交",type:"warning"},approved:{label:"已通过",type:"success"},rejected:{label:"已驳回",type:"danger"},paused:{label:"已暂停",type:"warning"},canceled:{label:"已撤销",type:"danger"},confirmed:{label:"已确认",type:"success"},archived:{label:"已归档",type:"info"}},W={not_submitted:{label:"未提交",type:"info"},draft:{label:"草稿",type:"info"},submitted:{label:"待确认",type:"warning"},confirmed:{label:"已确认",type:"success"},rejected:{label:"已驳回",type:"danger"}};function Z(a,e){const[c,V]=a.split("-").map(Number),b=new Date(c,V-1+e,1);return`${b.getFullYear()}-${String(b.getMonth()+1).padStart(2,"0")}`}const Q=T(()=>{const a=ut(),e=new Map;return U.value.forEach(c=>{c.status!=="CANCELED"&&(e.has(c.planMonth)||e.set(c.planMonth,c))}),Array.from({length:7},(c,V)=>{const b=Z(d.value,V-3),_=e.get(b),P=_?H[C(_.status)]:H.draft;return{id:_==null?void 0:_.id,month:b,title:(_==null?void 0:_.title)||"该月暂无计划",isCurrent:b===a,hasPlan:!!_,statusLabel:_?P.label:"未创建",statusType:_?P.type:"info"}})}),$=T(()=>U.value.find(a=>a.planMonth===d.value&&a.status!=="CANCELED")),X=T(()=>d.value>=ut()),D=T(()=>{var a;return(((a=s.value)==null?void 0:a.items)??[]).map(e=>({...e,latestResult:F.value.find(c=>c.monthPlanItemId===e.id)}))}),nt=T(()=>Number(D.value.reduce((a,e)=>a+Number(e.performanceWeight||0),0).toFixed(2))),x=T(()=>D.value.filter(a=>!!a.latestResult).length),n=T(()=>!k.value&&D.value.some(a=>!!a.latestResult||pt(a))),l=T(()=>{var e;const a=(e=s.value)==null?void 0:e.status;return a==="draft"||a==="rejected"?"编辑":""}),y=T(()=>{var e;const a=(e=s.value)==null?void 0:e.status;return a==="draft"?"补齐任务内容后提交审批":a==="rejected"?"根据审批意见修改并重新提交":a==="submitted"?`当前计划正在等待${k.value?"部门负责人":"直属领导"}审批`:a==="approved"?"计划已通过，可直接在下方查看任务或提交成果":"当前计划已完成流程处理"});function C(a){return{DRAFT:"draft",PENDING:"submitted",APPROVED:"approved",REJECTED:"rejected",PAUSED:"paused",CANCELED:"canceled"}[a]||"draft"}function g(a){return H[a]}function Y(a){return W[a]}function tt(a){return a==="draft"||a==="rejected"}function st(a){d.value=a}function z(a){E.push(q(a))}function I(){if($.value)return z($.value.id);E.push({path:q(),query:{planMonth:d.value}})}function ot(){if(s.value&&tt(s.value.status))return z(s.value.id)}function et(a){return a.latestResult?Y(a.latestResult.status):W.not_submitted}function p(a){return Number(a||0).toFixed(2)}function pt(a){var e;return((e=s.value)==null?void 0:e.status)==="approved"&&a.status==="approved"&&(!a.latestResult||a.latestResult.status==="rejected")}function xt(a){s.value&&E.push({path:`/employee/results/workspace/MONTH/${s.value.id}`,query:{month:s.value.planMonth,taskId:String(a)}})}async function Et(){if(s.value)try{await Lt.confirm("撤回后月计划恢复为草稿，可修改后重新提交。确认撤回？","撤回月计划",{type:"warning"}),await Yt(s.value.id),R.success("月计划已撤回为草稿"),await G()}catch(a){a!=="cancel"&&a!=="close"&&(R.error(a instanceof Error?a.message:"撤回失败"),await G())}}async function Ct(){f.value=!0,N.value="",s.value=null,F.value=[];try{if(!$.value)return;const[a,e]=await Promise.all([yt($.value.id),k.value?Promise.resolve({resultVersions:[]}):ht(),S.loadOne($.value.id)]);s.value=a,F.value=e.resultVersions.filter(c=>c.monthPlanId===a.id)}catch(a){N.value=a instanceof Error?a.message:"月计划加载失败，请稍后重试"}finally{f.value=!1}}async function G(){f.value=!0,N.value="",s.value=null,F.value=[];try{const a=Ut({mine:!0}).then(b=>b??[]),e=a.then(async b=>{const _=b.find(rt=>rt.planMonth===d.value);if(!_)return null;const[P,it]=await Promise.all([yt(_.id),k.value?Promise.resolve({resultVersions:[]}):ht()]);return{detail:P,options:it}}),[c,V]=await Promise.all([a,e]);U.value=c,await S.load(c.map(b=>b.id)),s.value=(V==null?void 0:V.detail)??null,F.value=V?V.options.resultVersions.filter(b=>b.monthPlanId===V.detail.id):[]}catch(a){N.value=a instanceof Error?a.message:"月计划加载失败，请稍后重试"}finally{f.value=!1}}return Rt(d,Ct),jt(G),(a,e)=>{var mt;const c=_t,V=Mt,b=Ft,_=Bt,P=qt,it=zt,rt=It;return m(),w("section",ye,[h(k)?(m(),w("div",he,[t("div",null,[t("h1",ge,o(h(k)?"我的月计划":"月计划"),1),e[2]||(e[2]=t("p",{class:"page-subtitle"},"按月编制计划、跟进审批并推进成果提交。",-1))]),t("div",_e,[i(c,{icon:h(ct),loading:f.value,"aria-label":"刷新月计划",onClick:G},{default:u(()=>[...e[3]||(e[3]=[v("刷新",-1)])]),_:1},8,["icon","loading"]),i(c,{type:"primary",icon:h(ft),onClick:I},{default:u(()=>[v(o($.value?"打开月计划":"新建月计划"),1)]),_:1},8,["icon"])])])):A("",!0),N.value?(m(),j(V,{key:1,class:"dashboard-alert",type:"warning",closable:!1,"show-icon":"",title:N.value},null,8,["title"])):A("",!0),t("section",be,[t("div",we,[e[5]||(e[5]=t("div",null,[t("h2",null,"选择月份"),t("p",null,"最近三个月、本月和未来三个月的计划状态。")],-1)),t("div",ke,[i(b,{modelValue:d.value,"onUpdate:modelValue":e[0]||(e[0]=r=>d.value=r),type:"month","value-format":"YYYY-MM",placeholder:"选择月份"},null,8,["modelValue"]),h(k)?A("",!0):(m(),w(K,{key:0},[i(c,{icon:h(ct),loading:f.value,"aria-label":"刷新月计划",onClick:G},{default:u(()=>[...e[4]||(e[4]=[v("刷新",-1)])]),_:1},8,["icon","loading"]),i(c,{type:"primary",icon:h(ft),onClick:I},{default:u(()=>[v(o($.value?"打开月计划":"新建月计划"),1)]),_:1},8,["icon"])],64))])]),t("div",xe,[(m(!0),w(K,null,bt(Q.value,r=>(m(),w("button",{key:r.month,type:"button",class:vt(["employee-month-card",{"is-active":r.month===d.value,"is-current":r.isCurrent,"is-empty":!r.hasPlan}]),"aria-pressed":r.month===d.value,onClick:at=>st(r.month)},[t("span",Ce,o(r.month===d.value?"已选择":r.isCurrent?"本月":"月份"),1),t("strong",null,[v(o(r.month.slice(5)),1),e[6]||(e[6]=t("small",null,"月",-1))]),t("span",Me,o(r.month.slice(0,4)),1),t("span",Ne,o(r.title),1),i(_,{size:"small",type:r.statusType},{default:u(()=>[v(o(r.statusLabel),1)]),_:2},1032,["type"])],10,Ee))),128))])]),Dt((m(),w("section",Ve,[s.value?(m(),w(K,{key:0},[t("header",Pe,[t("div",Te,[e[7]||(e[7]=t("span",{class:"employee-month-workspace__kicker"},"本月行动摘要",-1)),t("h2",null,o(((mt=$.value)==null?void 0:mt.title)||`${s.value.planMonth} 月计划`),1),t("div",Se,[t("div",$e,[i(_,{type:g(s.value.status).type},{default:u(()=>[v("计划："+o(g(s.value.status).label),1)]),_:1},8,["type"]),i(_,{type:Y(s.value.resultStatus).type},{default:u(()=>[v("成果："+o(Y(s.value.resultStatus).label),1)]),_:1},8,["type"]),i(se,{review:h(S).reviewFor(s.value.id),loading:h(S).loading.value,onView:lt},null,8,["review","loading"])]),t("span",Ae,o(y.value),1)])]),t("div",Re,[t("div",je,[l.value?(m(),j(c,{key:0,type:"primary",onClick:ot},{default:u(()=>[v(o(l.value),1)]),_:1})):A("",!0),s.value.status==="submitted"?(m(),j(c,{key:1,type:"warning",plain:"",onClick:Et},{default:u(()=>[...e[8]||(e[8]=[v("撤回计划",-1)])]),_:1})):A("",!0),i(ve,{detail:s.value},null,8,["detail"])])])]),t("div",De,[t("div",null,[e[9]||(e[9]=t("span",null,"计划任务",-1)),t("strong",null,o(D.value.length),1),e[10]||(e[10]=t("small",null,"项",-1))]),t("div",null,[e[11]||(e[11]=t("span",null,"绩效权重",-1)),t("strong",null,o(nt.value),1),e[12]||(e[12]=t("small",null,"%",-1))]),h(k)?(m(),w("div",Be,[e[15]||(e[15]=t("span",null,"审批状态",-1)),t("strong",Fe,o(g(s.value.status).label),1)])):(m(),w("div",Le,[e[13]||(e[13]=t("span",null,"已提交成果",-1)),t("strong",null,o(x.value),1),e[14]||(e[14]=t("small",null,"项",-1))])),t("div",null,[e[16]||(e[16]=t("span",null,"整体完成",-1)),t("strong",null,o(s.value.resultSummary.overallCompletionRate),1),e[17]||(e[17]=t("small",null,"%",-1))])]),t("section",qe,[t("div",ze,[t("div",null,[e[18]||(e[18]=t("h2",null,"本月任务",-1)),t("p",null,o(h(k)?"查看本月全部任务与审批状态":"当前页面直接展示全部任务、期限和成果状态"),1)])]),D.value.length?(m(),w("div",Ie,[i(it,{data:D.value,"row-key":"id",border:"","table-layout":"auto",class:vt(["employee-month-readonly-table",{"has-actions":n.value}]),"aria-label":"月计划任务预览表"},{default:u(()=>[i(P,{label:"序号",width:"56",align:"center"},{default:u(({$index:r})=>[t("span",Ue,o(String(r+1).padStart(2,"0")),1)]),_:1}),i(P,{label:"工作计划内容","min-width":"300"},{default:u(({row:r})=>[i(wt,{class:"readonly-task-text readonly-task-text--primary",content:r.taskContent},null,8,["content"])]),_:1}),i(P,{label:"权重（%）",width:"86",align:"right"},{default:u(({row:r})=>[t("span",Oe,o(p(r.performanceWeight)),1)]),_:1}),i(P,{label:"考核标准（交付物、质量）","min-width":"320"},{default:u(({row:r})=>[t("p",He,o(h(kt)(r.deliverable,r.quality)),1)]),_:1}),i(P,{label:"完成时间段",width:"126",align:"center"},{default:u(({row:r})=>[t("span",We,o(h(dt)(r.estimatedStartDate,r.estimatedEndDate)),1)]),_:1}),i(P,{label:"预估价值",width:"112",align:"right"},{default:u(({row:r})=>[t("span",Ye,o(p(r.estimatedValue)),1)]),_:1}),n.value?(m(),j(P,{key:0,label:"成果",width:"128",align:"center"},{default:u(({row:r})=>[t("div",Ge,[i(_,{size:"small",type:et(r).type},{default:u(()=>[v(o(et(r).label),1)]),_:2},1032,["type"]),pt(r)?(m(),j(c,{key:0,link:"",type:"primary",onClick:at=>xt(r.id)},{default:u(()=>{var at;return[v(o(((at=r.latestResult)==null?void 0:at.status)==="rejected"?"重新提交":"提交成果"),1)]}),_:2},1032,["onClick"])):A("",!0)])]),_:1})):A("",!0)]),_:1},8,["data","class"])])):(m(),w("div",Ke,[...e[19]||(e[19]=[t("strong",null,"当前计划还没有任务",-1),t("p",null,"使用上方主操作进入编辑页，补充本月任务内容。",-1)])]))])],64)):(m(),w("div",Je,[t("span",null,o(d.value),1),e[21]||(e[21]=t("strong",null,"本月尚未创建计划",-1)),t("p",null,o(X.value?"创建月计划后，任务和成果提交情况将在这里显示。":"历史月份没有可查看的月计划记录。"),1),X.value?(m(),j(c,{key:0,type:"primary",onClick:I},{default:u(()=>[...e[20]||(e[20]=[v("新建月计划",-1)])]),_:1})):A("",!0)]))])),[[rt,f.value]]),i(oe,{modelValue:O.value,"onUpdate:modelValue":e[1]||(e[1]=r=>O.value=r),"plan-type":"MONTH",review:s.value?h(S).reviewFor(s.value.id):null,loading:h(S).loading.value,subject:s.value?`${s.value.planMonth} 月计划`:"",context:s.value?[s.value.employeeName,s.value.departmentName].filter(Boolean).join(" · "):""},null,8,["modelValue","review","loading","subject","context"])])}}});export{$a as default};
