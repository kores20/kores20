"""Global form rules, independent of templates and individual work-order data."""
from copy import deepcopy

DEFAULT_VISIBILITY = {
    **{c: {'mode': 'always', 'works': []} for c in ['01', 'inspection', '22', '03', '02', '26', 'LV']},
    **{c: {'mode': 'works', 'works': ['meter']} for c in ['24', '25', '16']},
    '05': {'mode': 'works', 'works': ['excavation']},
    '10': {'mode': 'works', 'works': ['joint', 'termination']},
    '09': {'mode': 'works', 'works': ['transformer', 'rmu']},
    '06-transformer': {'mode': 'works', 'works': ['transformer']},
    '06-rmu': {'mode': 'works', 'works': ['rmu']},
    '08': {'mode': 'works', 'works': ['earthing']},
    '07': {'mode': 'works', 'works': ['panel']},
}
FORM_ORDER = ['01', 'inspection', '22', '03', '02', '26', '24', '25', '16', '05', '10',
              '09', '06-transformer', '06-rmu', '08', '07', 'LV']


def form_config(settings, code):
    return (settings or {}).get('formConfigs', {}).get(code, {})


def visibility(settings, code):
    return deepcopy(form_config(settings, code).get('visibility', DEFAULT_VISIBILITY[code]))


def configured_forms(order, settings=None):
    if order.get('mode') == 'inspection':
        return ['inspection']
    works = set(order.get('works', []))
    result = []
    for code in FORM_ORDER:
        rule = visibility(settings, code)
        if rule['mode'] == 'always' or rule['mode'] == 'works' and works.intersection(rule.get('works', [])):
            result.append(code)
    return result


def check_override(config, key, order):
    """None means use the current order; an empty string means explicitly blank."""
    rule = config.get('checks', {}).get(key, {})
    mode = rule.get('mode', 'auto')
    if mode == 'blank':
        return ''
    if mode == 'fixed':
        return rule.get('value', '')
    if mode == 'works':
        return rule.get('value', '') if set(order.get('works', [])).intersection(rule.get('works', [])) else ''
    return None

# Which rendered fields consume each requested piece of work-order data.
FIELD_USES = {
    'date': {'01':['dateText','shortDate'],'inspection':['dateText'],'22':['dateText'],
             '03':['dateText','weekday'],'02':['dateText','weekday'],'LV':['dateText']},
    'contractor': {c:['contractor'] for c in ['01','22','03','02','26','24','16','05','10','06-transformer','06-rmu','08','09','07','LV']},
    'inspector': {c:['inspector'] for c in ['01','inspection','22','03','02','26','24','16','25','05','10','06-transformer','06-rmu','08','09','07']},
    'representative': {c:['representative'] for c in ['01','02','24','25','LV']},
    'description': {'inspection':['description'],'02':['description']},
    'permit': {'inspection':[]},
    'site': {c:['location'] for c in ['01','03','02','24','16','05','06-transformer','06-rmu','08','09','07']},
    'feeder': {c:['feeder'] for c in ['01','03','02','24','25','05','06-transformer','06-rmu','07']},
    'mainBreaker': {'25':['mainBreaker']}, 'operator': {'25':['operator']}, 'operatorId': {'25':['operatorId']},
    'excavationLength': {'05':['excavationSummary'],'02':['excavationLength']},
    'mvSize': {'05':['excavationSummary']}, 'mvLength': {'05':['excavationSummary']},
    'lvSize': {'05':['excavationSummary']}, 'lvLength': {'05':['excavationSummary']},
    'transformerKva': {'06-transformer':['foundationLabel']}, 'rmuCells': {'06-rmu':['foundationLabel']},
    'terminationNotes': {'10':['terminationNotes']},
}
FIELD_USES['contractor']['inspection']=['contractorLabel']
for _code,_count in [('inspection',2),('22',3),('03',3),('02',2),('16',2)]:
    FIELD_USES['inspector'][_code]=['inspector']+[f'inspector#{n}' for n in range(2,_count+1)]


def needs_field(key, forms, settings=None):
    if key not in FIELD_USES:return True
    relevant=[(c,keys) for c,keys in FIELD_USES[key].items() if c in forms]
    for code,keys in relevant:
        configured=form_config(settings,code).get('fields',{})
        if not keys or any(configured.get(k,{}).get('mode','auto')=='auto' for k in keys):return True
    return False


def custom_inputs(order, settings=None):
    from .schema import FORMS
    result=[]
    for code in configured_forms(order,settings):
        for key,rule in form_config(settings,code).get('fields',{}).items():
            if key.startswith('custom-') and rule.get('mode')=='input':
                result.append({'code':code,'id':key,'form':FORMS[code],'label':rule.get('label','خانة إضافية'),
                               'type':rule.get('inputType','text'),'required':rule.get('required',False)})
    return result
