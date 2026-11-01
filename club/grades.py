"""Shanghai academic semesters; promotion dates are February 15 and August 1."""
from datetime import date
from django.utils import timezone

GRADES = ['大一上', '大一下', '大二上', '大二下', '大三+']


def academic_year(day=None):
    day = day or timezone.localdate()
    return day.year if day >= date(day.year,8,1) else day.year-1


def validate_admission_year(value, day=None):
    if isinstance(value,str) and value.isascii() and value.isdigit():
        value = int(value)
    if type(value) is not int or not 2013 <= value <= academic_year(day):
        raise ValueError(f'请选择 2013–{academic_year(day)} 之间的入学年份')
    return value


def grade_for_year(year, day=None):
    day = day or timezone.localdate()
    if type(year) is not int or not 2013 <= year <= 9999 or day < date(year,8,1):
        return None
    spring = date(day.year,2,15) <= day < date(day.year,8,1)
    return min(4, (academic_year(day)-year)*2 + int(spring))


def chinese_number(number):
    digits = '零一二三四五六七八九'
    if 0 < number < 10: return digits[number]
    if 10 <= number < 100:
        tens,ones = divmod(number,10)
        return (digits[tens] if tens>1 else '')+'十'+(digits[ones] if ones else '')
    return str(number)


def cohort_data(profile, day=None):
    year = getattr(profile,'admission_year',None)
    if type(year) is not int or not 2013 <= year <= 9999: return None
    number = year-2012
    short = chinese_number(number)+'分队'
    return {'admission_year':year,'squad_number':number,'squad_label':'第'+short,'squad_short':short,
            'label':f'{year}级 · 第{short}'}


def boundaries(start, end):
    return sum(start < date(y, m, d) <= end for y in range(start.year, end.year + 1)
               for m, d in [(2, 15), (8, 1)])


def grade_on(profile, day=None):
    if getattr(profile,'admission_year',None) is not None:
        return grade_for_year(profile.admission_year,day)
    if profile is None or profile.grade is None or profile.grade_date is None:
        return None
    day = day or timezone.localdate()
    if profile.grade == 4:
        return 4
    delta = boundaries(profile.grade_date, day) if day >= profile.grade_date else -boundaries(day, profile.grade_date)
    return min(4, max(0, profile.grade + delta))


def validate_grade(value):
    if isinstance(value, str) and value in ['0','1','2','3','4']:
        return int(value)
    if type(value) is int and value in range(5):
        return value
    raise ValueError('请选择年级：大一上、大一下、大二上、大二下或大三+')


def is_retired(user_id):
    from .models import UserProfile
    return grade_on(UserProfile.objects.filter(user_id=user_id).first()) == 4
