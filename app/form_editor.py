"""Editable overlay fields. Original PDF pages are never modified."""
from contextvars import ContextVar
from copy import deepcopy
import math
import re


ACTIVE = ContextVar('form_overlay', default=None)
LABELS = {
    'region':'المنطقة', 'division':'الدائرة', 'contractor':'اسم المقاول', 'city':'المدينة',
    'id':'رقم أمر العمل', 'feeder':'المغذي / المحطة', 'location':'الموقع', 'dateText':'تاريخ التنفيذ',
    'shortDate':'اليوم والشهر', 'timeText':'الوقت', 'time':'الوقت', 'weekday':'اليوم',
    'representative':'اسم ممثل المقاول', 'inspector':'اسم المفتش / ممثل الاستشاري',
    'representativeSignature':'اسم ممثل المقاول في خانة التوقيع',
    'operatorSignature':'اسم مدخل البيانات في خانة التوقيع',
    'consultantShort':'اسم الاستشاري المختصر', 'handoverNotes':'ملاحظات التسليم',
    'department':'المنطقة والدائرة', 'contractorLabel':'المقاول', 'description':'وصف العمل',
    'note1':'ملاحظة البند الأول', 'reportNumber':'رقم التقرير', 'safetyArea':'منطقة تقرير السلامة',
    'safetyRecipient':'الجهة المرسل لها التقرير', 'attendance':'فترة التواجد', 'workCode':'رمز العمل',
    'customer':'اسم المشترك', 'excavationLength':'طول الحفر', 'operator':'مدخل البيانات',
    'operatorId':'الرقم الوظيفي', 'meterCount':'عدد العدادات', 'mainBreaker':'القاطع الرئيسي',
    'foundationLabel':'نوع القاعدة وسعتها', 'excavationSummary':'ملخص الحفر والكابلات',
    'equipmentNumber':'رقم المعدة', 'terminationNotes':'تفاصيل النهايات',
    'kind':'نوع الطلب', 'subscription':'رقم الاشتراك', 'place':'نوع المكان', 'previous':'السعة السابقة',
    'breaker':'قاطع العداد', 'serial':'رقم العداد', 'rating':'سعة العداد', 'digits':'عدد الخانات',
    'reading':'قراءة التركيب', 'removedReading':'قراءة الخلع', 'voltage':'الجهد', 'ctRatio':'نسبة المحولات',
    'multiplier':'معامل الضرب', 'maker':'الصانع', 'factory':'رقم المصنع', 'year':'سنة الصنع',
}


def label_for(key):
    if key.startswith('custom-'):return 'نص مضاف'
    if key.startswith('meters.'):
        _, row, field = key.split('.')
        return f'العداد {int(row)+1}: {LABELS.get(field,field)}'
    if key.startswith('safety.'):
        _, row, col = key.split('.')
        return f'السلامة {int(row)+1}: '+['الملاحظة','التوصية','الإجراء'][int(col)]
    if key.startswith('inspectionNote.'):
        return 'ملاحظة البند '+key.split('.')[-1]
    return LABELS.get(key,key)


class Overlay:
    def __init__(self, code, config=None, capture=False):
        self.code=code;self.config=config or {};self.capture=capture
        self.fields=[];self.counts={}

    def text(self, text, box, scale, key):
        key=key or 'text@'+','.join(f'{v:.2f}' for v in box)
        self.counts[key]=self.counts.get(key,0)+1
        field_id=key if self.counts[key]==1 else f'{key}#{self.counts[key]}'
        rule=self.config.get('fields',{}).get(field_id,{})
        default_box=list(box);box=list(rule.get('box',box))
        font_size=rule.get('fontSize',22*scale)
        mode=rule.get('mode','auto')
        if mode=='fixed':text=rule.get('text','')
        elif mode=='blank':text=''
        self.fields.append({'id':field_id,'kind':'text','key':key,'label':rule.get('label') or label_for(key),
                            'defaultBox':default_box,'box':box,'defaultFontSize':22*scale,
                            'fontSize':font_size,'mode':mode,'example':str(text or '')})
        return text,box,font_size/22

    def check(self, spec, value):
        rule=self.config.get('checks',{}).get(spec['id'],{})
        dx=rule.get('dx',0);dy=rule.get('dy',0)
        box=spec['box']
        self.fields.append({'id':spec['id'],'kind':'check','key':spec['id'],'label':spec['label'],
                            'defaultBox':box,'box':[box[0]+dx,box[1]+dy,box[2]+dx,box[3]+dy],
                            'mode':rule.get('mode','auto'),'example':value,
                            'fontSize':rule.get('fontSize',8),'defaultFontSize':8,
                            'condition':spec.get('condition'),
                            'options':spec.get('options',['نعم','لا','لا ينطبق',''])})
        return dx,dy,rule.get('fontSize',8)/8


def dimensions(code):
    return (792,612) if code=='25' else (842,595.22) if code=='LV' else (612,792)


def validate_config(code, config, field_specs=None):
    from .schema import WORKS
    if not isinstance(config,dict):raise ValueError('إعدادات النموذج غير صالحة')
    allowed={'visibility','fields','checks'}
    if set(config)-allowed:raise ValueError('إعداد غير معروف للنموذج')
    rule=config.get('visibility')
    if rule is not None:
        if not isinstance(rule,dict) or set(rule)-{'mode','works'}:raise ValueError('قاعدة الظهور غير صالحة')
        if rule.get('mode') not in ['always','works','never']:raise ValueError('اختر قاعدة ظهور صحيحة')
        works=rule.get('works',[])
        if not isinstance(works,list) or any(w not in WORKS or w=='lv' for w in works):raise ValueError('نوع العمل في قاعدة الظهور غير صحيح')
        if rule['mode']=='works' and not works:raise ValueError('اختر نوع عمل واحدًا على الأقل لظهور النموذج')
    width,height=dimensions(code)
    known={f['id']:f for f in field_specs or []}
    def number(v):return isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v)
    for kind in ['fields','checks']:
        entries=config.get(kind,{})
        if not isinstance(entries,dict) or len(entries)>400:raise ValueError('عدد خانات النموذج أكبر من المسموح')
        for key,entry in entries.items():
            if not isinstance(key,str) or len(key)>150 or not isinstance(entry,dict):raise ValueError('خانة غير صالحة')
            extra=key.startswith('custom-') and re.fullmatch(r'custom-[a-zA-Z0-9-]{1,70}',key)
            if field_specs is not None and key not in known and not (kind=='fields' and extra):raise ValueError('خانة غير معروفة في النموذج: '+key)
            if key in known and (known[key]['kind']=='text')!=(kind=='fields'):raise ValueError('نوع الخانة غير صحيح')
            if set(entry)-({'mode','text','box','fontSize','label','inputType','required'} if kind=='fields' else {'mode','value','works','dx','dy','fontSize'}):raise ValueError('إعداد خانة غير معروف')
            modes=['auto','fixed','blank','input'] if kind=='fields' else ['auto','fixed','blank','works']
            if entry.get('mode','auto') not in modes:raise ValueError('اختر طريقة تعبئة صحيحة')
            if 'fontSize' in entry and (not number(entry['fontSize']) or not 4<=entry['fontSize']<=100):raise ValueError('حجم الخط يجب أن يكون بين 4 و100')
            if kind=='fields':
                if not isinstance(entry.get('text',''),str) or len(entry.get('text',''))>500:raise ValueError('النص الثابت أطول من المسموح')
                b=entry.get('box')
                if b is not None and (not isinstance(b,list) or len(b)!=4 or not all(number(v) for v in b) or not (0<=b[0]<b[2]<=width and 0<=b[1]<b[3]<=height) or b[2]-b[0]<3 or b[3]-b[1]<3):raise ValueError('ضع خانة الكتابة داخل حدود الصفحة وبحجم مناسب')
                if extra and (b is None or entry.get('mode') not in ['fixed','blank','input']):raise ValueError('الخانة المضافة تحتاج موضعًا وطريقة تعبئة')
                if entry.get('mode')=='input' and not extra:raise ValueError('التعبئة الإضافية متاحة للخانات المضافة')
                if any(k in entry for k in ['label','inputType','required']) and not extra:raise ValueError('خصائص الإدخال متاحة للخانات المضافة فقط')
                if extra:
                    if not isinstance(entry.get('label',''),str) or len(entry.get('label',''))>100:raise ValueError('اسم الخانة يجب ألا يتجاوز 100 حرف')
                    if entry.get('mode')=='input' and not entry.get('label','').strip():raise ValueError('سمّ الخانة التي تريد تعبئتها في الأوامر')
                    if entry.get('inputType','text') not in ['text','number','date']:raise ValueError('نوع إدخال غير معروف')
                    if not isinstance(entry.get('required',False),bool):raise ValueError('حدد هل الخانة مطلوبة')
            else:
                if entry.get('value','') not in ['نعم','لا','لا ينطبق','']:raise ValueError('علامة فحص غير صالحة')
                if key in known and entry.get('value','') not in known[key].get('options',['نعم','لا','لا ينطبق','']):raise ValueError('هذا النوع من العلامات يقبل علامة صح أو فراغًا فقط')
                works=entry.get('works',[])
                if not isinstance(works,list) or any(w not in WORKS or w=='lv' for w in works):raise ValueError('شرط العمل غير صحيح')
                if entry.get('mode')=='works' and not works:raise ValueError('اختر الأعمال التي تظهر معها العلامة')
                dx=entry.get('dx',0);dy=entry.get('dy',0)
                if not number(dx) or not number(dy) or abs(dx)>width or abs(dy)>height:raise ValueError('موضع العلامة غير صالح')
                if key in known:
                    b=known[key]['defaultBox']
                    if b[0]+dx<0 or b[2]+dx>width or b[1]+dy<0 or b[3]+dy>height:raise ValueError('ضع العلامة داخل حدود الصفحة')
    return deepcopy(config)
