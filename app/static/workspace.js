'use strict';
// Missing data is staged here until the row's Save button is pressed.
const missingDrafts=new Map(),missingBusy=new Set(),deliveryBusy=new Set();
let missingPage=0;
const missingPageSize=40;

function showWorkspace(view){
 $('#renewal-view').classList.add('hidden');$('#nav-renewal').classList.remove('active');
 $('#orders-view').classList.toggle('hidden',view!=='orders');
 $('#missing-view').classList.toggle('hidden',view!=='missing');
 $('#nav-orders').classList.toggle('active',view==='orders');
 $('#nav-missing').classList.toggle('active',view==='missing');
 $('#page-title').textContent=view==='missing'?'جميع النواقص':'أوامر العمل';
 if(view==='missing')renderMissing();
}
function workflowTime(value){if(!value)return '';try{return new Intl.DateTimeFormat('ar-SA',{dateStyle:'short',timeStyle:'short',calendar:'gregory',timeZone:'Asia/Riyadh'}).format(new Date(value))}catch(e){return ''}}
function workflowInfo(o){return questionMap[o.uid]?.workflow||{stage:'draft',label:'مسودة'}}
function workflowButton(o){const w=workflowInfo(o);if(!['exported','delivered'].includes(w.stage))return '';return `<button class="small-btn workflow-action" data-delivery-uid="${esc(o.uid)}" data-delivery-stage="${w.stage==='delivered'?'exported':'delivered'}" ${deliveryBusy.has(o.uid)?'disabled':''}>${w.stage==='delivered'?'إلغاء علامة التسليم':'تم التسليم ✓'}</button>`}
function workflowCell(o){const w=workflowInfo(o);return `<div class="workflow-cell"><span class="workflow-badge stage-${esc(w.stage)}">${esc(w.label)}</span>${w.exportedAt?`<small>آخر تصدير: ${esc(workflowTime(w.exportedAt))}</small>`:''}${workflowButton(o)}</div>`}
function workflowDetail(o){const w=workflowInfo(o);return `<div class="workflow-detail"><div><b>مرحلة الأمر</b> <span class="workflow-badge stage-${esc(w.stage)}">${esc(w.label)}</span>${w.exportedAt?`<small>آخر تصدير: ${esc(workflowTime(w.exportedAt))}</small>`:''}${w.stage==='delivered'?`<small>تم التسليم: ${esc(workflowTime(w.deliveredAt))}</small>`:''}${w.stage==='needs_export'?'<p>تغيّرت بيانات الأمر أو تعبئة نماذجه. صدّر نسخة جديدة قبل التسليم.</p>':''}</div>${workflowButton(o)}</div>`}
function updateWorkflowDetail(){if($('#editor-workflow')&&current())$('#editor-workflow').innerHTML=workflowDetail(current())}
async function refreshOrderStatuses(){
 const snapshot=clone(orders),result=await api('/api/missing-batch',{orders:snapshot});
 for(const o of snapshot)if(JSON.stringify(orders.find(x=>x.uid===o.uid))===JSON.stringify(o))questionMap[o.uid]=result.statuses[o.uid];
 renderTable();updateMissingBadge();updateWorkflowDetail();
}
async function changeDelivery(uid,stage){
 if(deliveryBusy.has(uid))return;deliveryBusy.add(uid);renderTable();updateWorkflowDetail();
 try{const o=orders.find(o=>o.uid===uid);await persist(o);const snapshot=JSON.stringify(o),result=await api('/api/progress',{uid,stage});if(JSON.stringify(o)===snapshot)questionMap[uid]=result;toast(stage==='delivered'?'سجلنا تسليم الأمر':'ألغينا علامة التسليم')}
 finally{deliveryBusy.delete(uid);renderTable();updateWorkflowDetail()}
}
function missingRows(){return orders.flatMap(o=>(questionMap[o.uid]?.questions||[]).map(q=>({o,q,id:o.uid+':'+q.key})))}
function renderWorkspace(){
 const rows=missingRows();$('#side-missing-count').textContent=rows.length;
 const remaining=new Set(rows.map(row=>row.id));for(const id of missingDrafts.keys())if(!remaining.has(id))missingDrafts.delete(id);
 saveState();if(!$('#missing-view').classList.contains('hidden')&&!missingBusy.size)renderMissing();
}
function missingInput(row){
 const {q,id}=row,value=missingDrafts.get(id)??'';
 const label=`aria-label="${esc(q.label)}" data-missing-input="${esc(id)}"`;
 if(q.kind==='works')return `<div class="missing-work-options">${Object.entries(schema.works).filter(([k])=>k!=='lv').map(([k,v])=>`<label><input type="checkbox" ${label} value="${esc(k)}" ${Array.isArray(value)&&value.includes(k)?'checked':''}>${esc(v)}</label>`).join('')}</div>`;
 const options=q.kind==='assessment'?['نعم','لا','لا ينطبق']:q.kind==='voltage'?['230','400']:q.kind==='inspector'?schema.consultants.map(c=>c.name):null;
 if(options)return `<select ${label}><option value="">اختر الإجابة</option>${options.map(v=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(v)}${q.kind==='voltage'?' فولت':''}</option>`).join('')}</select>`;
 return `<input ${label} type="${q.kind==='date'?'date':q.kind==='meters'?'number':'text'}" ${q.kind==='meters'?'min="1" max="100" placeholder="عدد العدادات"':q.key==='id'?'inputmode="numeric" maxlength="9"':''} value="${esc(value)}" autocomplete="off">`;
}
function renderMissing(){
 if(!schema)return;
 const all=missingRows(),activeInput=document.activeElement,focusId=activeInput?.dataset.missingInput,selection=focusId&&activeInput.tagName==='INPUT'&&activeInput.type==='text'?[activeInput.selectionStart,activeInput.selectionEnd]:null;
 $('#missing-orders-count').textContent=new Set(all.map(r=>r.o.uid)).size;$('#missing-fields-count').textContent=all.length;
 const sections=[...new Set(all.map(r=>r.q.section))],section=$('#missing-section').value;
 $('#missing-section').innerHTML='<option value="all">جميع الأقسام</option>'+sections.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
 $('#missing-section').value=sections.includes(section)?section:'all';
 const search=$('#missing-search').value.trim().toLowerCase(),category=$('#missing-section').value;
 const rows=all.filter(r=>(category==='all'||r.q.section===category)&&(!search||[r.o.id,r.o.contractor,r.o.site,r.q.label].some(v=>String(v||'').toLowerCase().includes(search))));
 missingPage=Math.min(missingPage,Math.max(0,Math.ceil(rows.length/missingPageSize)-1));
 const start=missingPage*missingPageSize,visible=rows.slice(start,start+missingPageSize);
 $('#missing-body').innerHTML=visible.map(row=>`<tr data-missing-row="${esc(row.id)}" data-missing-uid="${esc(row.o.uid)}" data-missing-key="${esc(row.q.key)}"><td><button class="missing-order-link" data-open="${esc(row.o.uid)}">${esc(row.o.id||'مسودة جديدة')}</button><small>${esc([row.o.contractor,row.o.site].filter(Boolean).join(' · '))}</small></td><td><small>${esc(row.q.section)}</small><b>${esc(row.q.label)}</b></td><td class="missing-input-cell">${missingInput(row)}</td><td><div class="missing-row-actions"><button class="small-btn missing-save" data-missing-save="${esc(row.id)}">حفظ ✓</button><button class="missing-skip" data-missing-skip="${esc(row.id)}">أتركها فارغة</button></div></td></tr>`).join('');
 $('#missing-empty').classList.toggle('hidden',!!rows.length);$('#missing-empty').textContent=all.length?'لا توجد نواقص تطابق البحث.':'اكتملت النواقص. يمكنك مراجعة الأوامر وتصديرها.';
 $('#missing-page-info').textContent=rows.length?`عرض ${start+1}–${Math.min(start+missingPageSize,rows.length)} من ${rows.length} خانة`:'لا توجد خانات ناقصة';
 $('#missing-prev').disabled=missingPage===0;$('#missing-next').disabled=start+missingPageSize>=rows.length;
 if(focusId){const el=[...$('#missing-body').querySelectorAll('[data-missing-input]')].find(el=>el.dataset.missingInput===focusId);if(el){el.focus({preventScroll:true});if(selection)el.setSelectionRange(...selection)}}
}
function captureMissingInput(el){const id=el.dataset.missingInput;if(!id)return;if(el.type==='checkbox'){missingDrafts.set(id,[...el.closest('tr').querySelectorAll('input:checked')].map(x=>x.value))}else if(el.value)missingDrafts.set(id,el.value);else missingDrafts.delete(id);saveState()}
async function saveMissingRow(id,skip=false){
 if(missingBusy.has(id))return;
 const row=missingRows().find(r=>r.id===id);if(!row)return;
 const value=missingDrafts.get(id),{o,q}=row;
 if(!skip){
  if(q.kind==='works'){if(!Array.isArray(value)||!value.length)throw Error('اختر الأعمال المنفذة');set(o,'works',value);o.description=value.map(k=>schema.works[k]).join(' + ')}
  else if(q.kind==='meters'){const n=Number(value);if(!Number.isInteger(n)||n<1||n>100)throw Error('أدخل عدد عدادات من 1 إلى 100');set(o,'meters',Array.from({length:n},meter))}
  else{if(!String(value??'').trim())throw Error('اكتب المعلومة أو اختر تركها فارغة');set(o,q.key,String(value).trim())}
 }else o.skipped=[...new Set([...(o.skipped||[]),q.key])];
 missingBusy.add(id);
 const tr=[...$('#missing-body').children].find(tr=>tr.dataset.missingRow===id);tr?.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
 try{await persist(o);missingDrafts.delete(id);toast(skip?'تركنا الخانة فارغة':'حُفظت المعلومة')}
 finally{missingBusy.delete(id);renderWorkspace()}
}
document.addEventListener('click',async event=>{
 const b=event.target.closest('button');if(!b)return;
 try{
  if(b.id==='nav-missing'){showWorkspace('missing');return}
  if(b.id==='missing-back'){showWorkspace('orders');return}
  if(b.id==='missing-prev'||b.id==='missing-next'){missingPage+=b.id==='missing-next'?1:-1;renderMissing();return}
  if(b.dataset.missingSave){await saveMissingRow(b.dataset.missingSave);return}
  if(b.dataset.missingSkip){await saveMissingRow(b.dataset.missingSkip,true);return}
  if(b.dataset.deliveryUid)await changeDelivery(b.dataset.deliveryUid,b.dataset.deliveryStage);
 }catch(e){toast(e.message)}
});
document.addEventListener('input',event=>{const el=event.target;if(el.dataset.missingInput)captureMissingInput(el);if(el.id==='missing-search'){missingPage=0;renderMissing()}});
document.addEventListener('change',event=>{const el=event.target;if(el.dataset.missingInput)captureMissingInput(el);if(el.id==='missing-section'){missingPage=0;renderMissing()}if(el.id==='stage-filter')renderTable()});
document.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.dataset.missingInput&&event.target.tagName==='INPUT'&&event.target.type!=='checkbox'){event.preventDefault();saveMissingRow(event.target.dataset.missingInput).catch(e=>toast(e.message))}});
