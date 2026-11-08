from copy import deepcopy
from datetime import date
from .form_settings import configured_forms, form_config, check_override, needs_field, custom_inputs

QUESTIONS = [
    'بطاقات التعميد والتأهيل متوفرة وسارية لجميع فئات العاملين بالموقع',
    'نموذج فريق العمل للمقاول متوفر بالموقع',
    'إجراءات العمل الآمن متوفرة بلغة يفهمها العاملون',
    'تقييم المخاطر متوفر بلغة يفهمها العاملون',
    'اجتماع ما قبل بدء العمل منعقد وموقع من جميع العاملين',
    'أجهزة فحص للتأكد من العزل متوفرة',
    'تصريح العمل موجود ومكتمل بالشكل الصحيح',
    'الالتزام بنظام أقفال السلامة المعتمد',
    'الالتزام بالبطاقات التحذيرية',
    'مراعاة مسافة الخلوص الآمنة للخطوط الهوائية',
    'مستلم التصريح موجود بالموقع',
    'استخدام معدات الحماية من السقوط',
    'مهمات الوقاية الشخصية صالحة ومناسبة للعمل',
    'استخدام عدد يدوية معزولة',
    'الحمل الأقصى الآمن مسجل على معدات السلامة',
    'المركبات مطابقة لمواصفات الشركة',
    'المركبات سليمة من حيث الإطارات والزيوت والنظافة والترتيب',
    'طفايات الحريق صالحة ومفحوصة',
    'مسعف ومكافح حريق مؤهلان متوفران',
    'حقيبة إسعافات أولية كاملة متوفرة',
    'حواجز الحماية سليمة',
    'اللوحات الإرشادية في مكان وعلى بعد مناسبين',
    'الالتزام بعدم التدخين',
    'شعار الشركة موجود على المعدات والمركبات',
    'عدم إزالة المخلفات بعد انتهاء العمل (نعم = توجد مخلفات)',
    'إضافة معدات تأريض كافية بعد العزل للحماية من الكهرباء الراجعة',
]
FORMS = {'01':'تسليم الموقع','inspection':'تفتيش الموقع','22':'تفتيش السلامة الميداني',
 '03':'يومية الاستشاري','02':'يومية المقاول','26':'متابعة المخالفات','24':'إجراء 190',
 '25':'إجراء 200','16':'تركيب العداد','05':'الحفر والتمديد','10':'الوصلات والنهايات',
 '09':'المحول والوحدة الحلقية','06-transformer':'قاعدة المحول','06-rmu':'قاعدة الوحدة الحلقية',
 '08':'التأريض','07':'لوحة التوزيع','LV':'فحص الجهد المنخفض'}
WORKS = {'meter':'تركيب عداد','excavation':'حفر وتمديد','joint':'وصلة لحام',
 'termination':'نهاية طرفية','transformer':'محول','rmu':'وحدة حلقية','panel':'لوحة توزيع (بيلر)','earthing':'تأريض','lv':'فحص LV'}
LV_QUESTIONS = [
    'لوحة الجهد المنخفض مثبتة ومتوازنة',
    'نهايات كابلات لوحة الجهد المنخفض موصلة بصورة صحيحة',
    'تأريض محايد لوحة الجهد المنخفض مطابق للمعيار',
    'قاعدة لوحة الجهد المنخفض سليمة',
    'جميع توصيلات كابلات الجهد المنخفض محكمة وصحيحة',
    'توصيل تأريض كابلات الجهد المنخفض صحيح',
    'البيلر مثبت ومتوازن',
    'نهايات كابلات البيلر موصلة بصورة صحيحة',
    'تأريض محايد البيلر مطابق للمعيار',
    'قاعدة البيلر سليمة',
    'صندوق العداد مثبت بإحكام وعلى الارتفاع الصحيح',
    'قضيب التأريض مركب وموصل بصندوق العداد حسب المعيار',
    'أسلاك تأريض المشترك موصلة بالمحايد',
    'توصيلات الكابلات والأسلاك داخل صندوق العداد محكمة',
]

def lv_checks(o, settings=None):
    """Fixed LV ticks are derived at use time, including for existing orders."""
    works=set(o.get('works',[]));checked={5,6}
    if 'meter' in works:checked.update([11,12,13,14])
    if 'panel' in works:checked.update([7,8,9,10])
    config=form_config(settings,'LV');result=[]
    for n in range(1,15):
        override=check_override(config,f'LV.{n}',o)
        result.append(override if override is not None else 'نعم' if n in checked else '')
    return result

BASE = ['01','inspection','22','03','02','26']
PATTERNS = {
 'inspection':['لا' if n==1 else 'لا ينطبق' if n in [8,9,10,12,26] else 'نعم' for n in range(1,27)],
 '26':['نعم' if v==1 else 'لا' for v in [1,1,1,1,1,2,1,1,2,2,2,2,1]],
 '05':['نعم' if v==1 else 'لا' for v in [1,1,1,2,1,1,1,2,2,2,1,2,1,2,2,1,1,1,2,1,1,2,1,2,1]],
 '10-joint':['لا' if n in [3,7,8,11,13] else 'نعم' for n in range(1,15)],
 '24':['نعم']*9,'16':['نعم']*9,
}
DEFAULTS = {'region':'عسير','division':'الإنشاءات','consultantCompany':'روافد للاستشارات الهندسية',
 'consultantShort':'روافد','inspector':'علي الشهري','representative':'صدام محمد','contractor':'أفق البنية',
 'city':'أبها','time':'08:00','attendance':'8','handoverNotes':'لا يوجد',
 'safetyArea':'عسير - أبها','safetyRecipient':'إدارة الأمن والسلامة',
 'safetyRows':[['حواجز غير كافية','توريد','توريد'],['لوحات متهالكة','استبدال','استبدال'],['عدد غير معزولة','توريد','توريد']],
 'assessmentPatterns':PATTERNS,'fontScale':1.0}

def new_order(settings=None):
    d=deepcopy(DEFAULTS);d.update(deepcopy(settings or {}))
    d.pop('formConfigs',None)
    d.pop('signing',None)
    patterns=d.pop('assessmentPatterns',PATTERNS)
    d.update({'id':'','workCode':'801','consultantCode':'912','date':'','site':'','feeder':'',
      'description':'','permit':'','mainBreaker':'','voltage':'','operator':'','operatorId':'',
      'mode':'bundle','works':['meter'],'meters':[],'customFields':{},'skipped':[], 'assessments':deepcopy(patterns),
      'inspectionNotes':{'1':'عامل مشارك'},'excavationLength':'','mvSize':'','mvLength':'','lvSize':'','lvLength':'',
      'transformerKva':'','rmuCells':'','voltageKv':'','terminationNotes':'','equipmentNumber':''})
    return d

def selected_forms(o, settings=None):
    return configured_forms(o,settings)

def missing(o, settings=None):
    skipped=set(o.get('skipped',[]));qs=[];forms=selected_forms(o,settings)
    def add(key,label,kind='text',section='بيانات الأمر'):
        if kind!='assessment' and not needs_field(key,forms,settings):return
        cur=o
        for part in key.split('.'):
            try:cur=cur[int(part)] if isinstance(cur,list) else cur.get(part)
            except (TypeError,IndexError,AttributeError,ValueError):cur=None
        if (cur is None or str(cur).strip()=='' or cur==[]) and key not in skipped:
            qs.append({'key':key,'label':label,'kind':kind,'section':section})
    for key,label,kind in [('id','رقم أمر العمل','text'),('date','تاريخ التنفيذ / الزيارة','date'),('contractor','اسم المقاول','text'),('inspector','اسم المفتش وممثل الاستشاري','inspector'),('description','وصف العمل بالموقع','text')]:add(key,label,kind)
    add('permit','رقم تصريح العمل')
    if o.get('mode')!='inspection':
        for k,l in [('representative','ممثل ومهندس المقاول'),('site','الحي / الموقع'),('feeder','رقم المغذي / المحطة')]:add(k,l)
    works=set(o.get('works',[])) if o.get('mode')!='inspection' else set()
    if o.get('mode')!='inspection' and not works:add('works','ما الأعمال المنفذة في هذا الأمر؟','works')
    if any(code in forms for code in ['25','16']):
        for k,l,t in [('mainBreaker','سعة القاطع الرئيسي بالأمبير','text'),('operator','اسم مدخل البيانات','text'),('operatorId','الرقم الوظيفي لمدخل البيانات','text')]:add(k,l,t,'العدادات')
        if not o.get('meters'):add('meters','أضف بيانات العدادات','meters','العدادات')
        for i,m in enumerate(o.get('meters',[])):
            for k,l in [('serial','رقم العداد'),('breaker','قاطع العداد بالأمبير'),('subscription','رقم الاشتراك')]:add(f'meters.{i}.{k}',f'العداد {i+1}: {l}','text','العدادات')
            import re
            if m.get('serial') and not re.match(r'[A-Za-z]+\d{2}',str(m['serial'])):add(f'meters.{i}.year',f'العداد {i+1}: سنة الصنع','text','العدادات')
            if m.get('ct'):
                for k,l in [('rating','قوة العداد الفعلية'),('ctRatio','نسبة محولات التيار'),('multiplier','معامل الضرب')]:add(f'meters.{i}.{k}',f'العداد {i+1}: {l}','text','العدادات')
            if m.get('kind')=='إزالة':add(f'meters.{i}.removedReading',f'العداد {i+1}: قراءة الخلع','text','العدادات')
    if '25' in forms and not o.get('voltage'):
        meters=o.get('meters',[])
        fields=form_config(settings,'25').get('fields',{})
        if not meters or any(not m.get('voltage') and fields.get(f'meters.{i%10}.voltage',{}).get('mode','auto')=='auto' for i,m in enumerate(meters)):
            add('voltage','جهد المنخفض لهذا الأمر (فولت)','voltage','العدادات')
    if '05' in forms:
        for k,l in [('excavationLength','طول الحفر بالمتر'),('mvSize','مقاس كابل المتوسط (أو اتركه فارغًا)'),('mvLength','طول كابل المتوسط بالمتر'),('lvSize','مقاس كابل المنخفض (أو اتركه فارغًا)'),('lvLength','طول كابل المنخفض بالمتر')]:add(k,l,'text','الحفر والتمديد')
    for flag,key,label in [('transformer','transformerKva','سعة المحول kVA'),('rmu','rmuCells','عدد خلايا الوحدة الحلقية'),('termination','terminationNotes','مقاسات النهايات وعدد كل منها')]:
        if flag in works or {'transformer':'06-transformer','rmu':'06-rmu','termination':'10'}[flag] in forms:add(key,label,'text','الأعمال')
    for field in custom_inputs(o,settings):
        if field['required']:
            add(f"customFields.{field['code']}.{field['id']}",field['label'],
                'date' if field['type']=='date' else 'text',field['form']+' — خانات إضافية')
    # Checklist applicability is explicit; skipped answers remain empty in PDF.
    for code in selected_forms(o,settings):
        if code in ['25','01','02','03','22','LV']:continue
        count={'inspection':26,'26':13,'24':9,'16':9,'05':25,'08':7,'07':13,'09':31,'LV':14,'06-transformer':6,'06-rmu':6}.get(code,0)
        if code=='10':
            for tag,n in [('10-joint',14),('10-termination',13)]:
                if ('joint' if tag.endswith('joint') else 'termination') in works:
                    for i in range(n):
                        if check_override(form_config(settings,code),f'{tag}.{i+1}',o) is None:
                            add(f'assessments.{tag}.{i}',f'{FORMS[code]} — {"لحام" if tag.endswith("joint") else "نهايات"}: البند {i+1}','assessment','التقييمات')
        else:
            for i in range(count):
                if check_override(form_config(settings,code),f'{code}.{i+1}',o) is not None:continue
                if code=='24' and i==3 and not any(m.get('ct') for m in o.get('meters',[])):continue
                if code=='16' and (i==8 and not any(m.get('ct') for m in o.get('meters',[])) or i==5 and not o.get('temporaryMeter')):continue
                label=QUESTIONS[i] if code=='inspection' else f'{FORMS[code]} — البند {i+1}'
                add(f'assessments.{code}.{i}',label,'assessment','التقييمات')
    return qs
