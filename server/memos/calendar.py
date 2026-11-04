"""Fixed solar/lunar recurrence dates; views always reference the original record."""
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from lunar_python import Lunar, LunarMonth, Solar

ZONE=ZoneInfo('Asia/Shanghai')


def local_time(value):
    parsed=datetime.fromisoformat(value) if isinstance(value,str) else value
    return parsed.replace(tzinfo=ZONE) if parsed.tzinfo is None else parsed.astimezone(ZONE)


def anchor(day,clock=None):
    return datetime.combine(date.fromisoformat(day) if isinstance(day,str) else day,time.fromisoformat(clock or '00:00'),ZONE)


def dates(rule,start,end):
    origin=date.fromisoformat(rule['start_date']);until=date.fromisoformat(rule['until']) if rule.get('until') else end
    end=min(end,until);start=max(start,origin)
    if start>end:return []
    interval=rule['interval'];frequency=rule['frequency'];result=[]
    if frequency in ('daily','weekly'):
        step=interval*(7 if frequency=='weekly' else 1)
        first=max(0,((start-origin).days+step-1)//step)
        return [origin+timedelta(days=n*step) for n in range(first,(end-origin).days//step+1)]
    if frequency=='monthly':
        offset=(start.year-origin.year)*12+start.month-origin.month
        first=max(0,offset//interval)
        count=(end.year-origin.year)*12+end.month-origin.month
        for n in range(first,count//interval+1):
            year,month=divmod(origin.year*12+origin.month-1+n*interval,12);month+=1
            candidate=date(year,month,min(origin.day,monthrange(year,month)[1]))
            if start<=candidate<=end:result.append(candidate)
    elif rule['calendar']=='solar':
        for year in range(origin.year,end.year+1,interval):
            candidate=date(year,origin.month,min(origin.day,monthrange(year,origin.month)[1]))
            if start<=candidate<=end:result.append(candidate)
    else:
        lunar_origin=Solar.fromYmd(origin.year,origin.month,origin.day).getLunar().getYear()
        for year in range(lunar_origin,min(end.year+1,2100)+1,interval):
            month=rule['lunar_month'];wanted=-month if rule['lunar_leap'] else month
            lunar_month=LunarMonth.fromYm(year,wanted) or LunarMonth.fromYm(year,month)
            solar=Lunar.fromYmd(year,lunar_month.getMonth(),min(rule['lunar_day'],lunar_month.getDayCount())).getSolar()
            candidate=date(solar.getYear(),solar.getMonth(),solar.getDay())
            if start<=candidate<=end:result.append(candidate)
    return result


def record_dates(record,start,end):
    if record.get('recurrence'):return dates(record['recurrence'],start,end)
    schedule=record.get('schedule')
    day=date.fromisoformat(schedule['start_date']) if schedule else None
    return [day] if day and start<=day<=end else []


def schedule_occurrences(record,start,end):
    schedule=record['schedule'];original=date.fromisoformat(schedule['start_date'])
    stop=date.fromisoformat(schedule['end_date']) if schedule.get('end_date') else original
    span=(stop-original).days
    result=[]
    for occurrence in record_dates(record,start-timedelta(days=span),end):
        until=occurrence+timedelta(days=span)
        last=until-timedelta(days=1) if schedule.get('end_time')=='00:00' and until>occurrence else until
        day=max(start,occurrence)
        while day<=min(end,last):
            result.append({'memo_id':record['id'],'record_version':record['version'],'date':day.isoformat(),'start_date':occurrence.isoformat(),'end_date':until.isoformat() if schedule.get('end_date') else None,'start_time':schedule['start_time'],'end_time':schedule['end_time']})
            day+=timedelta(days=1)
    return result
