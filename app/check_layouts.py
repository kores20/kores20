"""Checkbox anchors shared by PDF rendering and the visual form editor."""
from .schema import QUESTIONS, LV_QUESTIONS


def check_specs(code, src, order):
    result=[]
    def add(tag,n,y,yes=None,no=None,na=None,box=None,condition=None,label=None,default=None):
        positions={k:[x,y] for k,x in [('نعم',yes),('لا',no),('لا ينطبق',na)] if x is not None}
        bounds=box
        if bounds is None:
            xs=[p[0] for p in positions.values()]
            bounds=[min(xs)-6,y-6,max(xs)+8,y+6]
        result.append({'id':f'{tag}.{n}','tag':tag,'index':n-1,'positions':positions,'textBox':box,
                       'box':bounds,'label':label or f'البند {n}'+(' — نهايات' if tag=='10-termination' else ' — لحام' if tag=='10-joint' else ''),
                       'condition':condition,'default':default,
                       'options':['نعم',''] if default is not None else ['نعم','لا','لا ينطبق','']})
        # Keep the reviewed placement of textual "not applicable" answers.
        old_na={'26':(294,-5.8,352,5.2),'24':(193,-5,281,5),'16':(281,-4.5,339,4.5),
                '10-joint':(306,-5,374,5),'10-termination':(306,-5,374,5),
                '07':(305,-5,365,5),'09':(298,-4.8,329,5.2)}.get(tag)
        if old_na:result[-1]['naBox']=[old_na[0],y+old_na[1],old_na[2],y+old_na[3]]
    def rows(xmin,ymin,ymax):
        return {int(w[4]):(w[1]+w[3])/2 for w in src.get_text('words') if w[0]>xmin and ymin<w[1]<ymax and w[4].isdigit()}
    if code=='inspection':
        for n,y in rows(565,170,650).items():add(code,n,y,260,225,190,label=QUESTIONS[n-1])
    elif code=='26':
        for i,y in enumerate([174.5,198,238,279,308,354,407,446,481,510,534,553,569]):add(code,i+1,y+13.8,339,307)
    elif code=='24':
        for i,y in enumerate([224,250,276,302,328,354,388,430,471]):add(code,i+1,y+14,261,214,condition='ct' if i==3 else None)
    elif code=='16':
        ct=any(m.get('ct') for m in order.get('meters',[]))
        add('meterType',1,141,225 if ct else 365,label='علامة نوع صندوق العداد',default='نعم')
        result[-1]['box']=[219,135,373,147]
        add('meterConnection',1,159,371 if ct else 314,label='علامة نوع توصيل العداد',default='نعم')
        result[-1]['box']=[308,153,379,165]
        for n,y in rows(520,250,446).items():add(code,n,y,296,329,condition='ct' if n==9 else 'temporaryMeter' if n==6 else None)
    elif code=='05':
        for n,y in rows(550,180,605).items():add(code,n,y,box=[279,y-5,307,y+5])
    elif code=='10':
        for tag,x,lo,hi in [('10-joint',552,174,353),('10-termination',567,377,545)]:
            for n,y in rows(x,lo,hi).items():add(tag,n,y,322,357,condition='joint' if tag.endswith('joint') else 'termination')
    elif code in ['06-transformer','06-rmu']:
        for i,y in enumerate([211.6,258.6,277.5,296.4,319.9,343.4]):add(code,i+1,y+13.8,344,313)
    elif code=='07':
        for i,y in enumerate([216,259.5,281.2,301.1,322.8,344.6,364.5,386.2,407.9,428,449.7,471.4,491.3]):add(code,i+1,y,316,350)
    elif code=='08':
        for i,y in enumerate([205.8,226.5,247.1,272.7,303.9,334.8,359.9]):add(code,i+1,y+13.8,322,356)
    elif code=='09':
        ys=[189.35,201.35,213.35,228.85,247.75,263.15,275.15,287.15,299.15,314.65,342.05,354.05,366.05,378.05,390.05,402.05,414.05,438.05,450.05,465.55,480.95,492.95,516.95,528.95,540.95,552.95,564.95,576.95,588.95,612.95,624.95]
        for i,y in enumerate(ys):add(code,i+1,y+13.8,313,341)
    elif code=='LV':
        for i,y in enumerate([170.5,185.5,200.6,215.6,230.6,245.7,260.7,275.8,290.8,305.9,320.9,336,351,366]):add(code,i+1,y,610.6,710,label=LV_QUESTIONS[i])
    elif code=='03':
        add('participantConnections',1,159.8,469,label='توصيلات المشتركين',default='نعم')
    return result
