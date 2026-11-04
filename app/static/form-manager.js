'use strict';
let fm=null,fmPreviewTimer,fmImageURL,fmSerial=0,fmDrag=null;
function fmError(message=''){ $('#fm-error').textContent=message;$('#fm-error').classList.toggle('hidden',!message) }
function fmWorkChoices(values,attribute){return Object.entries(schema.works).filter(([k])=>k!=='lv').map(([k,v])=>`<label><input type="checkbox" ${attribute}="${k}" ${values.includes(k)?'checked':''}>${esc(v)}</label>`).join('')}
async function openFormManager(){
 if(!fm){const response=await api('/api/forms');fm={forms:response.forms,drafts:{},metadata:{},dirty:new Set(),previewValues:{},code:response.forms[0].code,selected:null};for(const f of fm.forms)fm.drafts[f.code]=clone(f.config)}
 $('#fm-model').innerHTML=fm.forms.map(f=>`<option value="${f.code}">${esc(f.name)}</option>`).join('');$('#fm-model').value=fm.code;
 $('#fm-example').innerHTML='<option value="">بيانات مثال — لا تحفظ كأمر عمل</option>'+orders.map(o=>`<option value="${esc(o.uid)}">${esc(o.id||'مسودة')} — ${esc(o.contractor)}</option>`).join('');
 modal('form-manager');await fmLoad(fm.code)
}
function fmConfig(){return fm.drafts[fm.code]}
function fmMeta(){return fm.metadata[fm.code]}
function fmFields(){return (fmMeta()?.fields||[]).filter(f=>!f.id.startsWith('custom-')||fmConfig().fields?.[f.id])}
function fmLabel(field){return fmEntry(field).label||field.label}
function fmField(){return fmFields().find(f=>f.id===fm.selected)}
function fmEntry(field,create=false){const group=field.kind==='text'?'fields':'checks',config=fmConfig();if(create){config[group]||={};config[group][field.id]||={}}return config[group]?.[field.id]||{}}
function fmBox(field){const entry=fmEntry(field);if(field.kind==='text')return entry.box||field.defaultBox;const b=field.defaultBox,dx=entry.dx||0,dy=entry.dy||0;return [b[0]+dx,b[1]+dy,b[2]+dx,b[3]+dy]}
function fmDirty(){fm.dirty.add(fm.code);fmError();fmDirtyLabel()}
function fmDirtyLabel(){$('#fm-dirty').textContent=fm.dirty.size?`${fm.dirty.size} نموذج بتعديلات غير محفوظة`:'لا توجد تعديلات غير محفوظة';$('#fm-save').disabled=!fm.dirty.size}
async function fmLoad(code){
 fm.code=code;fm.selected=null;fmDrag=null;fmError();$('#fm-preview-state').textContent='جارٍ تحميل النموذج…';$('#fm-properties').innerHTML='';$('#fm-boxes').innerHTML='';
 try{if(!fm.metadata[code])fm.metadata[code]=await api('/api/forms/editor?code='+encodeURIComponent(code));if(!fm||fm.code!==code)return;
 const rule=fmConfig().visibility||fmDefaultVisibility(code);
 $('#fm-visibility').value=rule.mode;$('#fm-visibility-works').innerHTML=fmWorkChoices(rule.works||[],'data-fm-visibility-work');$('#fm-visibility-works').classList.toggle('hidden',rule.mode!=='works');
 $('#fm-search').value='';fmSelectOptions();fmDirtyLabel();await fmPreview()
 }catch(e){fmError(e.message);$('#fm-preview-state').textContent='تعذر تحميل المعاينة'}
}
function fmSelectOptions(){const query=$('#fm-search').value.trim();const fields=fmFields().filter(f=>!query||fmLabel(f).includes(query)||f.id.includes(query));
 if(!fields.some(f=>f.id===fm.selected))fm.selected=fields[0]?.id||null;
 $('#fm-field').innerHTML=['text','check'].map(kind=>`<optgroup label="${kind==='text'?'الكتابة':'علامات الفحص'}">${fields.filter(f=>f.kind===kind).map(f=>`<option value="${esc(f.id)}" ${f.id===fm.selected?'selected':''}>${esc(fmLabel(f))}</option>`).join('')}</optgroup>`).join('');fmProperties();fmBoxes()
}
function fmFocusField(){const el=$$('.fm-box').find(x=>x.dataset.fmBox===fm.selected);el?.scrollIntoView({block:'nearest',inline:'nearest'})}
function fmProperties(){const field=fmField();if(!field){$('#fm-properties').innerHTML='<p class="muted">لا توجد خانة مطابقة للبحث.</p>';return}const entry=fmEntry(field),mode=entry.mode||'auto',b=fmBox(field),text=field.kind==='text';
 const options=[...(!field.id.startsWith('custom-')?[['auto',text?'من بيانات الأمر':'من الأمر / القاعدة الحالية']]:[['input','تُعبأ لكل أمر عمل']]),['fixed',text?'نص ثابت':'تقييم ثابت'],['blank','تترك فارغة'],...(!text?[['works','تقييم حسب نوع العمل']]:[])];
 const num=(key,label,value,disabled=false)=>`<label class="field"><span>${label}</span><input type="number" data-fm-number="${key}" step="0.5" value="${Math.round(value*10)/10}" ${disabled?'disabled':''}></label>`;
 $('#fm-properties').innerHTML=`${field.id.startsWith('custom-')?`<label class="field"><span>اسم الخانة</span><input id="fm-label" maxlength="100" value="${esc(entry.label||'')}" placeholder="مثال: اسم الفني"></label>`:''}<label class="field"><span>طريقة التعبئة</span><select id="fm-mode">${options.map(([k,label])=>`<option value="${k}" ${mode===k?'selected':''}>${label}</option>`).join('')}</select></label>${text?`<label class="field ${mode!=='fixed'?'hidden':''}"><span>النص الثابت</span><textarea id="fm-text" rows="3" maxlength="500">${esc(entry.text||'')}</textarea></label>`:`<label class="field ${!['fixed','works'].includes(mode)?'hidden':''}"><span>التقييم</span><select id="fm-check-value">${(field.options||['نعم','لا','لا ينطبق','']).map(v=>`<option value="${v}" ${(entry.value??'نعم')===v?'selected':''}>${v||'فارغ'}</option>`).join('')}</select></label><div class="fm-work-choices ${mode!=='works'?'hidden':''}">${fmWorkChoices(entry.works||[],'data-fm-check-work')}</div>`}${text&&mode==='input'?`<label class="field"><span>نوع البيانات</span><select id="fm-input-type">${[['text','نص'],['number','أرقام'],['date','تاريخ']].map(([k,label])=>`<option value="${k}" ${(entry.inputType||'text')===k?'selected':''}>${label}</option>`).join('')}</select></label><label class="work-option"><input type="checkbox" id="fm-required" ${entry.required?'checked':''}>خانة مطلوبة ضمن النواقص</label><label class="field"><span>قيمة للمعاينة فقط</span><input id="fm-input-preview" type="${entry.inputType==='date'?'date':'text'}" value="${esc(fm.previewValues[fm.code]?.[field.id]||'')}" maxlength="500"></label>`:''}<p class="fm-source">${text?(mode==='input'?'تظهر هذه الخانة في «خاناتي» داخل كل أمر يستخدم هذا النموذج.':mode==='auto'?'يسحب النص من بيانات أمر العمل.':mode==='blank'?'تترك هذه الخانة بلا كتابة مضافة.':'يستخدم النص الثابت في كل أمر يظهر فيه النموذج.'):(field.condition==='ct'?'يطبق هذا البند عند وجود محولات تيار CT.':field.condition==='temporaryMeter'?'يطبق هذا البند عند وجود عداد مؤقت.':'التقييم الثابت يستخدم تلقائيًا في الأوامر التي يظهر فيها النموذج.')}</p><div class="fm-numbers">${num('x','أفقي',b[0])}${num('y','عمودي',b[1])}${num('width','العرض',b[2]-b[0],!text)}${num('height','الارتفاع',b[3]-b[1],!text)}${num('fontSize',text?'حجم الخط':'حجم العلامة',entry.fontSize||field.defaultFontSize)}</div><p class="muted">الكتابة تبقى داخل المربع. وسّع المربع إذا احتاج الخط مساحة أكبر.</p><button class="small-btn" id="fm-field-reset">${field.id.startsWith('custom-')?'حذف النص المضاف':'إرجاع إعدادات هذه الخانة'}</button>`
}
function fmBoxes(){if(!fmMeta())return;const meta=fmMeta(),show=$('#fm-show-boxes').checked;
 $('#fm-stage').style.aspectRatio=meta.width+'/'+meta.height;
 $('#fm-boxes').classList.toggle('hidden',!show);
 $('#fm-boxes').innerHTML=fmFields().map((f,i)=>{const b=fmBox(f);return `<div role="button" tabindex="0" class="fm-box ${f.kind} ${fm.selected===f.id?'selected':''}" data-fm-box="${esc(f.id)}" aria-label="${esc(fmLabel(f))}" title="${esc(fmLabel(f))}" style="left:${b[0]/meta.width*100}%;top:${b[1]/meta.height*100}%;width:${(b[2]-b[0])/meta.width*100}%;height:${(b[3]-b[1])/meta.height*100}%"><span>${i+1}</span>${f.kind==='text'?'<i class="fm-resize"></i>':''}</div>`}).join('')
}
function fmSchedule(){clearTimeout(fmPreviewTimer);fmPreviewTimer=setTimeout(fmPreview,450)}
async function fmPreview(){if(!fm||!fmMeta())return;const code=fm.code,seq=++fmSerial;$('#fm-preview-state').textContent='جارٍ تحديث المعاينة…';$('.fm-preview').classList.add('busy');
 try{const uid=$('#fm-example').value,order=uid?orders.find(o=>o.uid===uid):undefined;const blob=await api('/api/forms/preview',{code,config:fmConfig(),order,previewValues:fm.previewValues[code]||{}});if(!fm||fm.code!==code||seq!==fmSerial)return;
 const previous=fmImageURL;fmImageURL=URL.createObjectURL(blob);$('#fm-image').src=fmImageURL;if(previous)URL.revokeObjectURL(previous);$('#fm-preview-state').textContent='معاينة التعديلات الحالية';fmError()
 }catch(e){if(fm&&fm.code===code&&seq===fmSerial){fmError(e.message);$('#fm-preview-state').textContent='راجع الإعدادات لتحديث المعاينة'}}finally{if(seq===fmSerial)$('.fm-preview').classList.remove('busy')}
}
function fmStoreBox(field,box){const meta=fmMeta();let [x,y,x1,y1]=box;const w=Math.max(3,Math.min(meta.width,x1-x)),h=Math.max(3,Math.min(meta.height,y1-y));x=Math.max(0,Math.min(meta.width-w,x));y=Math.max(0,Math.min(meta.height-h,y));const entry=fmEntry(field,true);if(field.kind==='text')entry.box=[x,y,x+w,y+h];else{entry.dx=x-field.defaultBox[0];entry.dy=y-field.defaultBox[1]}}
async function fmSave(){if(fm.saving)return;fm.saving=true;const button=$('#fm-save');button.disabled=true;$('#fm-cancel').disabled=true;fmError();try{for(const code of [...fm.dirty]){const result=await api('/api/forms/save',{code,config:fm.drafts[code]});settings=result.settings;fm.dirty.delete(code);const item=fm.forms.find(f=>f.code===code);item.config=clone(fm.drafts[code]);item.visibility=clone(fm.drafts[code].visibility||fmDefaultVisibility(code))}await reload();toast('حُفظت إعدادات النماذج لجميع الأوامر على هذا الموقع');fmDirtyLabel()}catch(e){fmError(e.message);fmDirtyLabel()}finally{fm.saving=false;$('#fm-cancel').disabled=false}}
async function fmClick(event){const button=event.target.closest('button');if(!button||!$('#form-manager').contains(button)||!fm||fm.saving)return;
 try{switch(button.id){
 case 'fm-save':await fmSave();break;
 case 'fm-cancel':clearTimeout(fmPreviewTimer);fmSerial++;fm=null;$('#form-manager').close();break;
 case 'fm-original':original(fm.code);break;
 case 'fm-reset':fm.drafts[fm.code]={};delete fm.metadata[fm.code];fmDirty();await fmLoad(fm.code);break;
 case 'fm-field-reset':{const field=fmField();if(!field)return;delete fmConfig()[field.kind==='text'?'fields':'checks']?.[field.id];fmDirty();fmSelectOptions();fmSchedule();break}
 case 'fm-add-field':{const id='custom-'+crypto.randomUUID(),meta=fmMeta(),box=[meta.width*.3,meta.height*.45,meta.width*.65,meta.height*.45+24];fmConfig().fields||={};fmConfig().fields[id]={mode:'input',label:'خانة جديدة',inputType:'text',required:false,box,fontSize:22};meta.fields.push({id,kind:'text',key:id,label:'خانة جديدة',defaultBox:box,defaultFontSize:22,example:''});fm.selected=id;$('#fm-search').value='';fmDirty();fmSelectOptions();fmFocusField();fmSchedule();toast('سمّ الخانة وحدد مكانها بسحب مربعها فوق النموذج');break}
 case 'fm-add-text':{const id='custom-'+crypto.randomUUID(),meta=fmMeta(),box=[meta.width*.3,meta.height*.45,meta.width*.65,meta.height*.45+24];fmConfig().fields||={};fmConfig().fields[id]={mode:'fixed',text:'نص ثابت',box,fontSize:22};meta.fields.push({id,kind:'text',key:id,label:'نص مضاف',defaultBox:box,defaultFontSize:22,example:'نص ثابت'});fm.selected=id;$('#fm-search').value='';fmDirty();fmSelectOptions();fmSchedule();break}
 case 'fm-all-yes':case 'fm-all-blank':{for(const f of fmFields().filter(f=>f.kind==='check')){const entry=fmEntry(f,true);entry.mode=button.id==='fm-all-yes'?'fixed':'blank';entry.value=button.id==='fm-all-yes'?'نعم':''}fmDirty();fmProperties();fmSchedule();break}
 }}catch(e){fmError(e.message)}
}
function fmDefaultVisibility(code){return fm.metadata[code].defaultVisibility||fm.forms.find(f=>f.code===code).defaultVisibility||{mode:'always',works:[]}}
function fmChange(event){if(!fm||fm.saving||!$('#form-manager').contains(event.target))return;const el=event.target;
 if(el.id==='fm-model'){fmLoad(el.value);return}if(el.id==='fm-example'){fmSchedule();return}if(el.id==='fm-show-boxes'){fmBoxes();return}if(el.id==='fm-zoom'){$('#fm-stage').style.width=Number(el.value)*100+'%';return}
 if(el.id==='fm-field'){fm.selected=el.value;fmProperties();fmBoxes();fmFocusField();return}
 if(el.id==='fm-visibility'||el.dataset.fmVisibilityWork){fmConfig().visibility={mode:$('#fm-visibility').value,works:$$('[data-fm-visibility-work]:checked').map(x=>x.dataset.fmVisibilityWork)};$('#fm-visibility-works').classList.toggle('hidden',$('#fm-visibility').value!=='works');fmDirty();return}
 const field=fmField();if(!field)return;
 if(el.id==='fm-mode'){const entry=fmEntry(field,true);entry.mode=el.value;if(el.value==='input'){entry.label||='خانة جديدة';entry.inputType||='text'}if(field.kind==='check'&&['fixed','works'].includes(el.value)&&entry.value===undefined)entry.value='نعم';fmDirty();fmProperties();fmSchedule();return}
 if(el.id==='fm-input-type'){fmEntry(field,true).inputType=el.value;fmDirty();fmProperties();fmSchedule();return}
 if(el.id==='fm-required'){fmEntry(field,true).required=el.checked;fmDirty();return}
 if(el.id==='fm-check-value'){fmEntry(field,true).value=el.value;fmDirty();fmSchedule();return}
 if(el.dataset.fmCheckWork){fmEntry(field,true).works=$$('[data-fm-check-work]:checked').map(x=>x.dataset.fmCheckWork);fmDirty();fmSchedule();return}
}
function fmInput(event){if(!fm||fm.saving||!$('#form-manager').contains(event.target))return;const el=event.target;if(el.id==='fm-search'){fmSelectOptions();return}const field=fmField();if(!field)return;
 if(el.id==='fm-label'){fmEntry(field,true).label=el.value;const option=[...$('#fm-field').options].find(x=>x.value===field.id);if(option)option.textContent=el.value||'خانة جديدة';fmDirty();fmBoxes();return}
 if(el.id==='fm-input-preview'){fm.previewValues[fm.code]||={};fm.previewValues[fm.code][field.id]=el.value;fmSchedule();return}
 if(el.id==='fm-text'){fmEntry(field,true).text=el.value;fmDirty();fmSchedule();return}
 if(el.dataset.fmNumber){const value=Number(el.value);if(!el.value||!Number.isFinite(value))return;const key=el.dataset.fmNumber;
 if(key==='fontSize'){fmEntry(field,true).fontSize=Math.max(4,Math.min(100,value))}else{const b=[...fmBox(field)],w=b[2]-b[0],h=b[3]-b[1];if(key==='x'){b[0]=value;b[2]=value+w}else if(key==='y'){b[1]=value;b[3]=value+h}else if(key==='width'){b[2]=b[0]+value}else{b[3]=b[1]+value}fmStoreBox(field,b)}fmDirty();fmBoxes();fmSchedule()}
}
document.addEventListener('click',fmClick);document.addEventListener('change',fmChange);document.addEventListener('input',fmInput);
$('#fm-boxes').addEventListener('pointerdown',event=>{if(!fm)return;const box=event.target.closest('[data-fm-box]');if(!box)return;event.preventDefault();const field=fmFields().find(f=>f.id===box.dataset.fmBox);fm.selected=field.id;fmProperties();$$('.fm-box').forEach(el=>el.classList.toggle('selected',el===box));$('#fm-field').value=field.id;const rect=$('#fm-stage').getBoundingClientRect();fmDrag={field,start:fmBox(field),x:event.clientX,y:event.clientY,rect,resize:event.target.classList.contains('fm-resize')};$('#fm-boxes').setPointerCapture(event.pointerId)});
$('#fm-boxes').addEventListener('pointermove',event=>{if(!fmDrag||!fm)return;const d=fmDrag,meta=fmMeta(),dx=(event.clientX-d.x)/d.rect.width*meta.width,dy=(event.clientY-d.y)/d.rect.height*meta.height;let box=d.resize?[d.start[0],d.start[1],d.start[2]+dx,d.start[3]+dy]:d.start.map((v,i)=>v+(i%2?dy:dx));fmStoreBox(d.field,box);const b=fmBox(d.field),element=$$('.fm-box').find(el=>el.dataset.fmBox===d.field.id);Object.assign(element.style,{left:b[0]/meta.width*100+'%',top:b[1]/meta.height*100+'%',width:(b[2]-b[0])/meta.width*100+'%',height:(b[3]-b[1])/meta.height*100+'%'});fmDirty()});
function fmEndDrag(){if(!fmDrag)return;fmDrag=null;fmProperties();fmBoxes();fmSchedule()}
$('#fm-boxes').addEventListener('pointerup',fmEndDrag);$('#fm-boxes').addEventListener('pointercancel',fmEndDrag);
$('#fm-boxes').addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.dataset.fmBox){event.preventDefault();fm.selected=event.target.dataset.fmBox;$('#fm-field').value=fm.selected;fmProperties();fmBoxes()}});
