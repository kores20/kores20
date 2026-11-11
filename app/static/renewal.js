'use strict';
const rn={id:null,job:null,page:null,field:null,options:null,loading:false,saving:false,pending:0,serial:0,timer:null,previewURL:null,chain:Promise.resolve(),dirty:false,polling:false};
function rnError(text=''){$('#rn-error').textContent=text;$('#rn-error').classList.toggle('hidden',!text)}
function rnProgress(label,value=0){$('#rn-status').classList.remove('hidden');$('#rn-status-label').textContent=label;$('#rn-progress').value=value}
function rnRemember(){try{rn.id?localStorage.setItem('forms-renewal-job',rn.id):localStorage.removeItem('forms-renewal-job')}catch(e){}}
async function openRenewal(){
 $('#orders-view').classList.add('hidden');$('#missing-view').classList.add('hidden');$('#renewal-view').classList.remove('hidden');$('#nav-orders').classList.remove('active');$('#nav-missing').classList.remove('active');$('#nav-renewal').classList.add('active');$('#page-title').textContent='تجديد النماذج';
 if(!rn.options)rn.options=await api('/api/renewal/options');
 if(!rn.id){try{rn.id=localStorage.getItem('forms-renewal-job')}catch(e){}if(rn.id){try{rn.job=await api('/api/renewal/'+rn.id);await rnResume()}catch(e){rn.id=null;rnRemember();rnError(e.message)}}}
}
async function rnUpload(){
 if(rn.loading||rn.polling)return;
 const file=$('#rn-file').files[0];if(!file)throw Error('اختر ملف PDF أولًا');
 if(file.size>25000000)throw Error('الحد الأقصى 25 ميغابايت');
 await rnFlush();rn.loading=true;if(rn.job)rnSummary();$('#rn-upload').disabled=true;rnError();
 try{const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));rnProgress('جارٍ رفع الملف وقراءة النماذج…');const result=await api('/api/renewal/upload',{filename:file.name,base64:btoa(binary)});rn.id=result.id;rn.page=null;rn.job=null;rnRemember();$('#renewal-view').classList.remove('rn-reviewing');$('#rn-review').classList.add('hidden');await rnPoll()}
 finally{rn.loading=false;$('#rn-upload').disabled=false;if(rn.job)rnSummary()}
}
async function rnPoll(){
 if(rn.polling)return;rn.polling=true;
 try{while(true){rn.job=await api('/api/renewal/'+rn.id);rnProgress(rn.job.message,rn.job.done/rn.job.total*100);if(rn.job.state==='error')throw Error(rn.job.message);if(rn.job.state==='ready'){await rnLoadPage(rn.page?.index||0);break}await new Promise(resolve=>setTimeout(resolve,1000))}}
 finally{rn.polling=false}
}
async function rnResume(){if(rn.job.state==='working')await rnPoll();else if(rn.job.state==='ready')await rnLoadPage(0);else throw Error(rn.job.message)}
function rnSummary(){
 $('#rn-review').inert=rn.loading;const pages=rn.job.pages;$('#rn-filename').textContent=rn.job.filename;$('#rn-reviewed-count').textContent=`${pages.filter(p=>p.confirmed).length} / ${pages.length} صفحات تمت مراجعتها`;
 $('#rn-page').innerHTML=pages.map(p=>`<option value="${p.index}" ${rn.page?.index===p.index?'selected':''}>${p.index+1}. ${esc(p.name)} ${p.confirmed?'✓':''}</option>`).join('');
 $('#rn-prev').disabled=!rn.page||rn.page.index===0;$('#rn-next').disabled=!rn.page||rn.page.index===pages.length-1;
 $('#rn-export').disabled=rn.loading||rn.dirty||pages.some(p=>!p.confirmed);
 $('#rn-original').href='/api/renewal/'+rn.id+'/original';
}
function rnUpdatePageSummary(){if(rn.page&&rn.job){rn.job.pages[rn.page.index]={...rn.job.pages[rn.page.index],confirmed:rn.page.confirmed,keepOriginal:rn.page.keepOriginal};rnSummary()}}
async function rnLoadPage(index){
 await rnFlush();rn.loading=true;rnSummary();rnError();
 try{rn.page=await api('/api/renewal/'+rn.id+'/page?page='+index);rn.field=null;rn.serial++;rn.dirty=false;
  $('#rn-review').classList.remove('hidden');$('#renewal-view').classList.add('rn-reviewing');$('.rn-upload').classList.add('hidden');$('#rn-model-options').open=!rn.page.code;$('#rn-status').classList.add('hidden');
  $('#rn-model').innerHTML='<option value="">اختر النموذج إذا لم نتعرف عليه</option>'+Object.entries(rn.options.forms).map(([code,name])=>`<option value="${code}" ${rn.page.code===code?'selected':''}>${esc(name)}</option>`).join('');
  $('#rn-layout').value=rn.page.layout;$('#rn-layout option[value="legacy"]').disabled=!rn.options.legacyForms.includes(rn.page.code);
  $('#rn-rotation').value=rn.page.rotation;$('#rn-keep-original').checked=rn.page.keepOriginal;$('#rn-confirm').checked=rn.page.confirmed;
  $('#rn-page-warning').textContent=rn.page.warnings.join(' ');$('#rn-source-image').src='/api/renewal/'+rn.id+'/source?page='+index+'&v='+Date.now();
  $('#rn-field-search').value='';rnFields();rnSummary();await rnPreview();
 }finally{rn.loading=false;rnSummary()}
}
function rnFields(){
 const search=$('#rn-field-search').value.trim().toLowerCase(),fields=rn.page.fields.filter(f=>!search||f.label.toLowerCase().includes(search));
 if(!fields.some(f=>f.id===rn.field))rn.field=fields.find(f=>f.hasInk&&f.kind!=='image'&&!f.unmapped)?.id||fields[0]?.id||null;
 $('#rn-field').innerHTML=fields.map(f=>`<option value="${esc(f.id)}" ${f.id===rn.field?'selected':''}>${esc(f.label)} ${f.kind==='image'||f.unmapped?'— راجع':f.mode==='blank'?'— فارغة':'— منقولة'}</option>`).join('');
 $('#rn-properties').classList.toggle('hidden',!rn.field||rn.page.keepOriginal);$('#rn-add-field').disabled=rn.page.keepOriginal;rnProperties();
}
function rnSelected(){return rn.page?.fields.find(f=>f.id===rn.field)}
function rnProperties(){
 const field=rnSelected();if(!field){rnBoxes();return}
 $('#rn-field-label').textContent=field.label;
 const modes=field.kind==='check'?{'image':'نقل الكتابة وعلامات الفحص كما هي',...(field.editableValues===false?{}:{'نعم':'نعم','لا':'لا','لا ينطبق':'لا ينطبق'}),'blank':'تركها فارغة'}:{'image':'نقل الكتابة من الملف القديم','text':'كتابة نص بديل','blank':'تركها فارغة'};
 $('#rn-mode').innerHTML=Object.entries(modes).map(([v,t])=>`<option value="${v}" ${field.mode===v?'selected':''}>${t}</option>`).join('');
 $('#rn-text').value=field.text;$('#rn-raw').checked=field.raw;$('#rn-text-wrap').classList.toggle('hidden',field.mode!=='text');$('#rn-raw-wrap').classList.toggle('hidden',field.mode!=='image');
 $('#rn-box-inputs').innerHTML=['sourceBox','box'].map(key=>{const b=field[key];return `<div class="rn-box-group"><strong>${key==='sourceBox'?'من الصفحة القديمة':'إلى الصفحة الجديدة'}</strong>${[['x','أفقي',b[0]],['y','رأسي',b[1]],['w','العرض',b[2]-b[0]],['h','الارتفاع',b[3]-b[1]]].map(([part,label,value])=>`<label class="field"><span>${label}</span><input type="number" min="0" step="0.5" data-rn-rect="${key}" data-rn-part="${part}" value="${Number(value.toFixed(1))}"></label>`).join('')}</div>`}).join('');
 rnBoxes();
}
function rnBoxes(){
 const f=rnSelected();for(const [key,id] of [['sourceBox','rn-source-box'],['box','rn-target-box']]){const box=$('#'+id);box.classList.toggle('hidden',!f||rn.page.keepOriginal);if(f){const b=f[key];box.style.left=b[0]/rn.page.width*100+'%';box.style.top=b[1]/rn.page.height*100+'%';box.style.width=(b[2]-b[0])/rn.page.width*100+'%';box.style.height=(b[3]-b[1])/rn.page.height*100+'%'}}
}
function rnChanged(){rn.serial++;rn.dirty=true;saveState();rn.page.confirmed=false;$('#rn-confirm').checked=false;$('#rn-save-state').textContent='تعديلات بانتظار الحفظ والمعاينة';rnUpdatePageSummary();clearTimeout(rn.timer);rn.timer=setTimeout(()=>rnFlush().then(rnPreview).catch(e=>rnError(e.message)),650)}
async function rnFlush(){
 clearTimeout(rn.timer);rn.timer=null;
 if(!rn.page||!rn.dirty){await rn.chain;return}
 const index=rn.page.index,serial=rn.serial,fields=clone(rn.page.fields);rn.dirty=false;rn.pending++;rn.saving=true;saveState();$('#rn-save-state').textContent='جارٍ حفظ نقل الخانات…';
 rn.chain=rn.chain.catch(()=>{}).then(()=>api('/api/renewal/save',{id:rn.id,page:index,changes:{fields,keepOriginal:rn.page?.index===index?rn.page.keepOriginal:false}}));
 try{await rn.chain;if(rn.page?.index===index&&rn.serial===serial){$('#rn-save-state').textContent='حُفظت التعديلات';rnUpdatePageSummary()}}
 catch(e){if(rn.page?.index===index)rn.dirty=true;throw e}
 finally{rn.pending--;rn.saving=rn.pending>0;saveState()}
}
async function rnPreview(){
 if(!rn.page)return;const index=rn.page.index,serial=rn.serial,id=rn.id;
 $('#rn-preview-state').textContent='جارٍ تجهيز المعاينة…';
 let blob;try{blob=await api('/api/renewal/'+id+'/preview?page='+index)}catch(e){if(rn.id!==id)return;throw e}
 if(rn.id!==id||rn.page?.index!==index||rn.serial!==serial)return;
 if(rn.previewURL)URL.revokeObjectURL(rn.previewURL);rn.previewURL=URL.createObjectURL(blob);$('#rn-target-image').src=rn.previewURL;$('#rn-preview-state').textContent=rn.page.keepOriginal?'ستبقى هذه الصفحة كما هي':'معاينة النقل إلى النموذج الجديد';rnBoxes();
}
async function rnConfirm(){const index=rn.page.index,serial=rn.serial,confirmed=$('#rn-confirm').checked,keepOriginal=rn.page.keepOriginal;await rnFlush();rn.chain=rn.chain.catch(()=>{}).then(()=>api('/api/renewal/save',{id:rn.id,page:index,changes:{confirmed,keepOriginal}}));const page=await rn.chain;if(rn.page.index===index&&rn.serial===serial){rn.page.confirmed=page.confirmed;rnUpdatePageSummary();$('#rn-save-state').textContent=page.confirmed?'سُجلت مراجعة الصفحة':'الصفحة تحتاج مراجعة'}}
async function rnModel(){
 await rnFlush();const code=$('#rn-model').value;if(!code)throw Error('اختر النموذج الجديد أو احتفظ بالصفحة الأصلية');
 rn.loading=true;rnSummary();rnProgress('جارٍ مطابقة الصفحة وإعادة تحديد الخانات…');
 try{const layout=rn.options.legacyForms.includes(code)?$('#rn-layout').value:'current';const p=await api('/api/renewal/model',{id:rn.id,page:rn.page.index,code,rotation:Number($('#rn-rotation').value),layout});rn.page=p;rn.job.pages[p.index]={...p};delete rn.job.pages[p.index].fields;rn.dirty=false;await rnLoadPage(p.index)}finally{rn.loading=false;rnSummary();$('#rn-status').classList.add('hidden')}
}
function rnAddField(){const id='renew-custom-'+crypto.randomUUID(),f={id,label:'خانة نقل إضافية',kind:'text',sourceBox:[100,200,260,230],box:[100,200,260,230],fontSize:22,mode:'image',text:'',hasInk:false,raw:true};rn.page.fields.push(f);rn.field=id;rnFields();rnChanged()}
async function rnExport(){
 await rnFlush();rn.loading=true;rnSummary();$('#rn-export').disabled=true;rnError();
 try{await api('/api/renewal/export',{id:rn.id});while(true){const job=await api('/api/renewal/'+rn.id);rnProgress(`تجهيز الملف الجديد: ${job.exportDone} / ${job.total}`,job.exportDone/job.total*100);if(job.exportState==='error')throw Error('تعذر تجهيز الملف. راجع الصفحات وحاول مرة أخرى');if(job.exportState==='idle')throw Error('توقف تجهيز الملف؛ اضغط تنزيل النماذج المجددة للمحاولة مجددًا');if(job.exportState==='done'){const link=document.createElement('a');link.href='/api/renewal/'+rn.id+'/download';link.download='renewed-forms.pdf';document.body.append(link);link.click();link.remove();toast('النسخة الجديدة جاهزة — بدأ التحميل');break}await new Promise(resolve=>setTimeout(resolve,900))}}
 finally{rn.loading=false;rnSummary();$('#rn-status').classList.add('hidden')}
}
document.addEventListener('click',async event=>{const button=event.target.closest('button');if(!button)return;try{
 if(button.id==='nav-renewal'){await openRenewal();return}
 if(button.id==='rn-new-file'){$('.rn-upload').classList.remove('hidden');$('.rn-upload').scrollIntoView({behavior:'smooth',block:'center'});return}
 if(button.id==='rn-upload'){await rnUpload();return}
 if(button.id==='rn-prev'||button.id==='rn-next'){await rnLoadPage(rn.page.index+(button.id==='rn-next'?1:-1));return}
 if(button.id==='rn-unreviewed'){const p=rn.job.pages.find(p=>!p.confirmed);if(p)await rnLoadPage(p.index);return}
 if(button.id==='rn-model-apply'){await rnModel();return}
 if(button.id==='rn-add-field'){rnAddField();return}
 if(button.id==='rn-export'){await rnExport();return}
 if(button.id==='rn-delete'){await rnFlush();await api('/api/renewal/delete',{id:rn.id});rn.id=null;rn.page=null;rn.job=null;rnRemember();$('#renewal-view').classList.remove('rn-reviewing');$('.rn-upload').classList.remove('hidden');$('#rn-review').classList.add('hidden');$('#rn-status').classList.add('hidden');toast('حُذفت نسخة جلسة التجديد؛ ملفك الأصلي على جهازك لم يتغير')}
 }catch(e){rnError(e.message)}});
document.addEventListener('change',async event=>{const el=event.target;try{
 if(el.id==='rn-zoom'){$('#rn-review').style.setProperty('--rn-zoom',el.value);return}
 if(el.id==='rn-page'){await rnLoadPage(Number(el.value));return}
 if(el.id==='rn-field'){rn.field=el.value;rnProperties();return}
 if(el.id==='rn-mode'){rnSelected().mode=el.value;rnProperties();rnChanged();return}
 if(el.id==='rn-raw'){rnSelected().raw=el.checked;rnChanged();return}
 if(el.id==='rn-confirm'){await rnConfirm();return}
 if(el.id==='rn-keep-original'){rn.page.keepOriginal=el.checked;rnFields();rnChanged();await rnFlush();await rnPreview();return}
 if(el.id==='rn-model'){$('#rn-layout option[value="legacy"]').disabled=!rn.options.legacyForms.includes(el.value);if(!rn.options.legacyForms.includes(el.value))$('#rn-layout').value='current'}
 }catch(e){rnError(e.message)}});
document.addEventListener('input',event=>{const el=event.target;if(el.id==='rn-field-search'){rnFields();return}if(el.id==='rn-text'){rnSelected().text=el.value;rnChanged();return}if(el.dataset.rnRect){const f=rnSelected(),key=el.dataset.rnRect,b=[...f[key]],part=el.dataset.rnPart,value=Number(el.value);if(!Number.isFinite(value))return;let x=b[0],y=b[1],w=b[2]-b[0],h=b[3]-b[1];if(part==='x')x=value;if(part==='y')y=value;if(part==='w')w=value;if(part==='h')h=value;x=Math.max(0,Math.min(rn.page.width-1,x));y=Math.max(0,Math.min(rn.page.height-1,y));w=Math.max(1,Math.min(rn.page.width-x,w));h=Math.max(1,Math.min(rn.page.height-y,h));f[key]=[x,y,x+w,y+h];if(key==='sourceBox'&&f.mode==='blank'){f.mode='image';$('#rn-mode').value='image';$('#rn-raw-wrap').classList.remove('hidden')}rnBoxes();rnChanged()}});
let rnDrag=null;
document.addEventListener('pointerdown',event=>{const handle=event.target.closest('[data-rn-resize]'),box=event.target.closest('[data-rn-box]');if(!box||!rnSelected()||rn.loading)return;event.preventDefault();const key=box.dataset.rnBox;rnDrag={key,resize:!!handle,x:event.clientX,y:event.clientY,box:[...rnSelected()[key]],rect:box.parentElement.getBoundingClientRect()};box.setPointerCapture(event.pointerId)});
document.addEventListener('pointermove',event=>{if(!rnDrag)return;const d=rnDrag,dx=(event.clientX-d.x)/d.rect.width*rn.page.width,dy=(event.clientY-d.y)/d.rect.height*rn.page.height,b=d.box;let x=b[0],y=b[1],w=b[2]-b[0],h=b[3]-b[1];if(d.resize){w=Math.max(1,Math.min(rn.page.width-x,w+dx));h=Math.max(1,Math.min(rn.page.height-y,h+dy))}else{x=Math.max(0,Math.min(rn.page.width-w,x+dx));y=Math.max(0,Math.min(rn.page.height-h,y+dy))}rnSelected()[d.key]=[x,y,x+w,y+h];rnBoxes()});
document.addEventListener('pointerup',()=>{if(rnDrag){if(rnDrag.key==='sourceBox'&&rnSelected().mode==='blank')rnSelected().mode='image';rnDrag=null;rnProperties();rnChanged()}});
window.addEventListener('beforeunload',event=>{if(rn.dirty||rn.saving){event.preventDefault();event.returnValue=''}});
