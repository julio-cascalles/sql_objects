from enum import Enum
import re


KEYWORD = {
    'SELECT': (',{}', 'SELECT *'),
    'FROM': ('{}', ''),
    'WHERE': ('{}AND ', ''),
    'GROUP BY': (',{}', ''),
    'ORDER BY': (',{}', ''),
    'LIMIT': (' ', ''),
}
SELECT, FROM, WHERE, GROUP_BY, ORDER_BY, LIMIT = KEYWORD.keys()
USUAL_KEYS = [SELECT, WHERE, GROUP_BY, ORDER_BY]


class SQLObject:
    def __init__(self, table_name: str=''):
        self.alias = ''
        self.values = {}
        self.key_field = ''
        self.set_table(table_name)

    def set_table(self, table_name: str):
        if not table_name:
            return
        if ' ' in table_name:
            table_name, self.alias = table_name.split()
        elif '_' in table_name:
            self.alias = ''.join(
                word[0].lower()
                for word in table_name.split('_')
            )
        else:
            self.alias = table_name.lower()[:3]
        self.values.setdefault(FROM, []).append(f'{table_name} {self.alias}')

    @property
    def table_name(self) -> str:
        return self.values[FROM][0].split()[0]
 
    def delete(self, search: str, keys: list=USUAL_KEYS):
        for key in keys:
            result = []
            for item in self.values.get(key, []):
                if search not in item:
                    result.append(item)
            self.values[key] = result


class Function:
    ...

class Field:
    prefix = ''

    @classmethod
    def format(cls, name: str, main: SQLObject) -> str:
        name = name.strip()
        if name == '_':
            name = '*'
        elif '.' not in name:
            name = f'{main.alias}.{name}'
        if Function in cls.__bases__:
            name = f'{cls.__name__}({name})'
        return f'{cls.prefix}{name}'

    @classmethod
    def add(cls, name: str, main: SQLObject):
        main.values.setdefault(SELECT, []).append(
            cls.format(name, main)
        )


class Avg(Function, Field):
    ...
class Min(Function, Field):
    ...
class Max(Function, Field):
    ...
class Sum(Function, Field):
    ...
class Count(Function, Field):
    ...

class Distinct(Field):
    prefix = 'DISTINCT '


class NamedField:
    def __init__(self, alias: str, class_type = Field):
        self.alias = alias
        if class_type not in [Field] + Field.__subclasses__():
            raise TypeError('class_type must be a Field (sub)class.')
        self.class_type = class_type

    def add(self, name: str, main: SQLObject):
        main.values.setdefault(SELECT, []).append(
            '{} as {}'.format(
                self.class_type.format(name, main),
                self.alias
            )
        )


class Table:
    def __init__(self, fields: list | str=[]):
        if isinstance(fields, str):
            fields = [f.strip() for f in fields.split(',')]
        self.fields = fields

    def add(self, name: str, main: SQLObject):
        main.set_table(name)
        for field in self.fields:
            Field.add(field, main)


class PrimaryKey:
    @staticmethod
    def add(name: str, main: SQLObject):
        main.key_field = name


class ForeignKey:
    references = {}

    def __init__(self, table_name: str):
        self.table_name = table_name

    @staticmethod
    def get_key(obj1: SQLObject, obj2: SQLObject) -> tuple:
        return obj1.table_name, obj2.table_name

    def add(self, name: str, main: SQLObject):
        key = self.get_key(main, self)
        ForeignKey.references[key] = (name, '')

    @classmethod
    def find(cls, obj1: SQLObject, obj2: SQLObject) -> tuple:
        key = cls.get_key(obj1, obj2)
        a, b = cls.references.get(key, ('', ''))
        return a, (b or obj2.key_field)


def quoted(value) -> str:
    if isinstance(value, str):
        value = f"'{value}'"
    return str(value)


class Where:
    prefix = ''

    def __init__(self, expr: str):
        self.expr = f'{self.prefix}{expr}'

    @classmethod
    def __constructor(cls, operator: str, value):
        return cls(expr=f'{operator} {quoted(value)}')

    @classmethod
    def eq(cls, value):
        return cls.__constructor('=', value)

    @classmethod
    def like(cls, value: str):
        return cls(f"LIKE '%{value}%'")
   
    @classmethod
    def gt(cls, value):
        return cls.__constructor('>', value)

    @classmethod
    def gte(cls, value):
        return cls.__constructor('>=', value)

    @classmethod
    def lt(cls, value):
        return cls.__constructor('<', value)

    @classmethod
    def lte(cls, value):
        return cls.__constructor('<=', value)
    
    @classmethod
    def is_null(cls):
        return cls('IS NULL')
    
    @classmethod
    def list(cls, values):
        if isinstance(values, list):
            values = ','.join(quoted(v) for v in values)
        return cls(f'IN ({values})')

    def add(self, name: str, main: SQLObject):
        main.values.setdefault(WHERE, []).append('{} {}'.format(
            Field.format(name, main), self.expr
        ))


class Not(Where):
    prefix = 'NOT '

    @classmethod
    def eq(cls, value):
        return Where.__constructor('<>', value)


class Case:
    def __init__(self, field: str):
        self.__conditions = {}
        self.default = None
        self.field = field

    def when(self, condition: Where, result: str):
        self.__conditions[result] = condition
        return self
    
    def else_value(self, default: str):
        self.default = default
        return self
    
    def add(self, name: str, main: SQLObject):
        field = Field.format(self.field, main)
        default = quoted(self.default)
        name = 'CASE \n{}\n\tEND AS {}'.format(
            '\n'.join(
                f'\t\tWHEN {field} {cond.expr} THEN {quoted(res)}'
                for res, cond in self.__conditions.items()
            ) + f'\n\t\tELSE {default}' if default else '',
            name
        )
        main.values.setdefault(SELECT, []).append(name)


class Options:
    def __init__(self, **values):
        self.__children: dict = values

    def add(self, logical_separator: str, main: SQLObject):
        """
        `logical_separator` must be AND or OR
        """
        conditions: list[str] = []
        child: Where
        for field, child in self.__children.items():
            conditions.append(' {} {} '.format(
                Field.format(field, main), child.expr
            ))
        main.values.setdefault(WHERE, []).append(
            '(' + logical_separator.join(conditions) + ')'
        )


class Between:
    def __init__(self, start, end):
        if start > end:
            start, end = end, start
        self.start = start
        self.end = end

    def add(self, name: str, main:SQLObject):
        Where.gte(self.start).add(name, main),
        Where.lte(self.end).add(name, main)


class SortType(Enum):
    ASC = ''
    DESC = ' DESC'

class OrderBy:
    sort: SortType = SortType.ASC
    @classmethod
    def add(cls, name: str, main: SQLObject):
        if main.alias:
            name = f'{main.alias}.{name}'
        main.values.setdefault(ORDER_BY, []).append(name + cls.sort.value)


class GroupBy:
    @staticmethod
    def add(name: str, main: SQLObject):
        main.values.setdefault(GROUP_BY, []).append(f'{main.alias}.{name}')


class Having:
    def __init__(self, function: Function, condition: Where):
        self.function = function
        self.condition = condition

    def add(self, name: str, main:SQLObject):
        main.values[GROUP_BY][-1] += ' HAVING {} {}'.format(
            self.function.format(name, main), self.condition.expr
        )
    
    @classmethod
    def avg(cls, condition: Where):
        return cls(Avg, condition)
    
    @classmethod
    def min(cls, condition: Where):
        return cls(Min, condition)
    
    @classmethod
    def max(cls, condition: Where):
        return cls(Max, condition)
    
    @classmethod
    def sum(cls, condition: Where):
        return cls(Sum, condition)
    
    @classmethod
    def count(cls, condition: Where):
        return cls(Count, condition)


class JoinType(Enum):
    INNER = ''
    LEFT = 'LEFT '
    RIGHT = 'RIGHT '
    FULL = 'FULL '

class Select(SQLObject):
    join_type: JoinType = JoinType.INNER
    REGEX = {}

    def __init__(self, table_name: str='', **values):
        super().__init__(table_name)
        self.__call__(**values)
        self.break_lines = True

    def add(self, name: str, main: SQLObject):
        def update_values(key: str, new_values: list):
            for value in new_values:
                old_values = main.values.get(key, [])
                if value not in old_values:
                    main.values[key] = old_values + [value]
        update_values(
            FROM, [
                '{jt}JOIN {tb} {a2} ON ({a1}.{f1} = {a2}.{f2})'.format(
                    jt=self.join_type.value,
                    tb=self.table_name,
                    a1=main.alias, f1=name,
                    a2=self.alias, f2=self.key_field
                )
            ] + self.values[FROM][1:]
        )
        for key in USUAL_KEYS:
            update_values(key, self.values.get(key, []))

    def __add__(self, other: SQLObject):
        foreign_field, primary_key = ForeignKey.find(self, other)
        if not foreign_field:
            foreign_field, primary_key = ForeignKey.find(other, self)
            if foreign_field:
                if primary_key:
                    PrimaryKey.add(primary_key, self)
                self.add(foreign_field, other)
                return other
            raise ValueError(f'No relationship found between {self.table_name} and {other.table_name}.')
        elif primary_key:
            PrimaryKey.add(primary_key, other)
        other.add(foreign_field, self)
        return self

    def __str__(self) -> str:
        TABULATION = '\n\t' if self.break_lines else ' '
        LINE_BREAK = '\n' if self.break_lines else ' '
        DEFAULT = lambda key: KEYWORD[key][1]
        FMT_SEP = lambda key: KEYWORD[key][0].format(TABULATION)
        select, _from, where, groupBy, orderBy, limit = [
            DEFAULT(key) if not self.values.get(key) else "{}{}{}{}".format(
                LINE_BREAK, key, TABULATION, FMT_SEP(key).join(self.values[key])
            ) for key in KEYWORD
        ]
        return f'{select}{_from}{where}{groupBy}{orderBy}{limit}'.strip()
   
    def __call__(self, **values):
        to_list = lambda x: x if isinstance(x, list) else [x]
        for name, params in values.items():
            for obj in to_list(params):
                obj.add(name, self)
        return self

    def __eq__(self, other: SQLObject) -> bool:
        def sorted_values(obj: SQLObject, key: str) -> list:
            return sorted(obj.values.get(key, []))
        for key in KEYWORD:
            if sorted_values(self, key) != sorted_values(other, key):
                return False
        return True

    def limit(self, row_count: int, offset: int=0):
        result = [str(row_count)]
        if offset > 0:
            result.append(f'OFFSET {offset}')
        self.values.setdefault(LIMIT, result)
        return self

    @classmethod
    def parse(cls, txt: str) -> list[SQLObject]:
        def find_last_word(pos: int) -> int:
            SPACE, WORD = 1, 2
            found = set()
            for i in range(pos, 0, -1):
                if txt[i] in [' ', '\t', '\n']:
                    if sum(found) == 3:
                        return i
                    found.add(SPACE)
                if txt[i].isalpha():
                    found.add(WORD)
                elif txt[i] == '.':
                    found.remove(WORD)
        def find_parenthesis(pos: int) -> int:
            for i in range(pos, len(txt)-1):
                if txt[i] == ')':
                    return i+1
        if not cls.REGEX:
            keywords = '|'.join(k + r'\b' for k in KEYWORD)
            flags = re.IGNORECASE + re.MULTILINE
            cls.REGEX['keywords'] = re.compile(f'({keywords})', flags)
            cls.REGEX['subquery'] = re.compile(r'(\w\.)*\w+ +in +\(SELECT.*?\)', flags)
        result = {}
        found = cls.REGEX['subquery'].search(txt)
        while found:
            start, end = found.span()
            inner = txt[start: end]
            if inner.count('(') > inner.count(')'):
                end = find_parenthesis(end)
                inner = txt[start: end]
            fld, *inner = re.split(r' IN | in', inner, maxsplit=1)
            if fld.upper() == 'NOT':
                pos = find_last_word(start)
                fld = txt[pos: start].strip() # [To-Do] Use the value of `fld`
                start = pos
                class_type = NotSelectIN
            else:
                class_type = SelectIN
            obj = class_type.parse(
                ' '.join(re.sub(r'^\(', '', s.strip()) for s in inner)
            )[0]
            result[obj.alias] = obj
            txt = txt[:start-1] + txt[end+1:]
            found = cls.REGEX['subquery'].search(txt)
        tokens = [t.strip() for t in cls.REGEX['keywords'].split(txt) if re.findall(r'\w+', t)]
        values = {k.upper(): v for k, v in zip(tokens[::2], tokens[1::2])}
        tables = [t.strip() for t in re.split('JOIN|LEFT|RIGHT|ON', values[FROM]) if t.strip()]
        for item in tables:
            if '=' in item:
                a1, f1, a2, f2 = [r.strip() for r in re.split('[().=]', item) if r]
                obj1: SQLObject = result[a1]
                obj2:SQLObject = result[a2]
                PrimaryKey.add(f2, obj2)
                ForeignKey(obj2.table_name).add(f1, obj1)
            else:
                obj = cls(item)
                for key in USUAL_KEYS:
                    if not key in values:
                        continue
                    separator = KEYWORD[key][0].format('')
                    fields = [
                        Field.format(fld, obj)
                        for fld in re.split(
                            separator, values[key]
                        ) if len(tables) == 1
                        or re.findall(f'\b*{obj.alias}[.]', fld)
                    ]
                    obj.values[key] = [ f for f in fields if f.strip() ]
                result[obj.alias] = obj
        return list( result.values() )

class SelectIN(Select):
    condition_class = Where

    def add(self, name: str, main: SQLObject):
        self.break_lines = False
        self.condition_class.list(self).add(name, main)

SubSelect = SelectIN

class NotSelectIN(SelectIN):
    condition_class = Not


if __name__ == "__main__":
    query_list = Select.parse("""
        SELECT
                cas.role,
                m.title,
                m.release_date,
                a.name as actors_name
        FROM
                Actor a
                LEFT JOIN Cast cas ON (a.cast = cas.id)
                LEFT JOIN Movie m ON (cas.movie = m.id)
        WHERE
                m.genre NOT in (SELECT g.id from Genres g where g.name in ('sci-fi', 'horror', 'distopia'))
                AND (m.hashtag = '#cult' OR m.awards LIKE '%Oscar%')
                AND m.id IN (select DISTINCT r.movie FROM Review r GROUP BY r.movie HAVING Avg(r.rate) > 4.5)
                AND a.age <= 69 AND a.age >= 45
        ORDER BY
                m.release_date
    """)
    for query in query_list:
        descr = ' {} ({}) '.format(
            query.table_name,
            query.__class__.__name__
        )
        print(descr.center(50, '-'))
        print(query)
    print('='*50)
