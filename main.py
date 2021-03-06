import sys
import os
from typing import NamedTuple
import re


DEBUG = False


def debug(*args, show=DEBUG, **kwargs):
    if show:
        print(*args, **kwargs)


IDENTIFIER_STR = r'[_a-zA-Z][_\w]*'

token_spec = {
    'IGNORE': r'//[^\n]r',
    'FUNCSTART': r'(((export )?(async )?function)|((public|private)( async)?( static)?)) (?P<funcname>%s)[^);]*\)([^{;]*{)' % IDENTIFIER_STR,
    'IDENTIFIER': IDENTIFIER_STR,
    'OPEN_PAREN': r'{',
    'CLOSE_PAREN': r'}',
    'NEWLINE': r'\n',
}
TOKEN_REGEX = re.compile('|'.join('(?P<%s>%s)' % pair for pair in token_spec.items()))


class Token(NamedTuple):
    kind: str
    value: str
    line: int
    column: int


class State:
    def __init__(self, ctx: 'Context'):
        self._ctx = ctx

    @property
    def ctx(self):
        return self._ctx

    def do(self) -> 'State':
        raise NotImplementedError


class Context:
    _state: State

    def __init__(self):
        self._state = None

    def _run_once(self):
        self._state = self._state.do()
        return self._state is not None

    def run(self, init: State):
        self._state = init
        while self._run_once():
            pass


class FileContext(Context):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.funcs = {}
        self.line_num = 1
        self.line_start = 0

    def run(self, *args):
        try:
            super().run(*args)
        except Exception as e:
            print(f"Error found after {self.path}:{self.token.line}:{self.token.column}: '{self.token.value}'", file=sys.stderr, flush=True)
            raise e


class EndState(State):
    def do(self) -> State:
        return None


class ErrorState(State):
    def __init__(self, ctx, err = None):
        super().__init__(ctx)
        self._err = err if err is not None else RuntimeError('Error state reached')

    def do(self) -> State:
        raise self._err


class FuncStartState(State):
    def do(self) -> State:
        name_mo = re.match(token_spec['FUNCSTART'], self.ctx.token.value)

        self.ctx.curr_func_name = name_mo['funcname']
        self.ctx.curr_func_ids = set()

        self.ctx.paren_level = 1 if '{' in self.ctx.token.value else 0

        return ReadFunc(self.ctx)


class EndFunc(State):
    def do(self) -> State:
        self.ctx.funcs[self.ctx.curr_func_name] = self.ctx.curr_func_ids

        return GetNextToken(self.ctx)


class GetNextToken(State):
    def _get_next_token(self) -> Token:
        mo = next(self.ctx.tok_itr)

        kind = mo.lastgroup
        value = mo.group()
        column = mo.start() - self.ctx.line_start

        tok = Token(kind, value, self.ctx.line_num, column)

        if kind == 'NEWLINE':
            self.ctx.line_start = mo.end()
            self.ctx.line_num += 1
        else:
            try:
                level = self.ctx.paren_level + 1
            except AttributeError:
                level = 1

            debug('\t' * level, tok)

        return tok

    def do(self) -> State:
        try:
            tok = self._get_next_token()
            self.ctx.token = tok

            if tok.kind == 'FUNCSTART':
                return FuncStartState(self.ctx)

            return GetNextToken(self.ctx)

        except StopIteration:
            return EndState(self.ctx)


class ReadFunc(GetNextToken):
    def do(self) -> State:
        tok = self._get_next_token()
        self.ctx.token = tok

        if tok.kind == 'FUNCSTART':
            name_mo = re.match(token_spec['FUNCSTART'], tok.value)
            nested_func_name = name_mo['funcname']

            print(f"Found nested function '{nested_func_name}' in '{self.ctx.path}'. Determine dependencies manually")

        elif tok.kind == 'IDENTIFIER':
            self.ctx.curr_func_ids.add(tok.value)

        elif tok.kind == 'OPEN_PAREN':
            self.ctx.paren_level += 1

        elif tok.kind == 'CLOSE_PAREN':
            self.ctx.paren_level -= 1

            if self.ctx.paren_level == 0:
                return EndFunc(self.ctx)

        return ReadFunc(self.ctx)


class ReadData(State):
    def do(self) -> State:
        debug()
        debug('File:', self.ctx.path)

        with open(self.ctx.path, 'r') as fp:
            data = fp.read()
            self.ctx.tok_itr = TOKEN_REGEX.finditer(data)
        return GetNextToken(self.ctx)


class Start(State):
    def do(self) -> State:
        return ReadData(self.ctx)


path_root = sys.argv[1]


def list_files(start: str):
    for root, dpaths, fpaths in os.walk(start):
        for full_path in map(lambda p: os.path.join(root, p), fpaths):
            yield full_path


class memoize_level:
    def __init__(self, func):
        self.func = func
        self.cache = {}

    def __call__(self, base_dict, key):
        cache_key = (tuple(sorted(base_dict.keys())), key)
        if val := self.cache.get(cache_key):
            return val

        result = self.func(base_dict, key)
        self.cache[cache_key] = result

        return result


@memoize_level
def get_level(base_dict, key):
    if len(base_dict[key]) == 0:
        return 0

    return 1 + max(map(lambda k: get_level(base_dict, k), base_dict[key]))


funcs = {}
func_paths = {}
for path in list_files(path_root):
    ctx = FileContext(path)
    ctx.run(Start(ctx))

    funcs |= ctx.funcs
    func_paths |= {f: path for f in ctx.funcs.keys()}

for fname, deps in funcs.items():
    debug(f'{fname} - {func_paths[fname]}')
    filtered_deps = set()
    for d in deps:
        if d in funcs.keys():
            debug('\t', d)
            filtered_deps.add(d)
        funcs[fname] = filtered_deps

levels = {}
for fname in funcs:
    l = get_level(funcs, fname)
    debug(fname, '->', l)
    levels.setdefault(l, set()).add(fname)

for level in sorted(levels.keys()):
    print(level)
    for fname in levels[level]:
        print(fname, '(', func_paths[fname], ')')
        if len(funcs[fname]) > 0:
            print(' -', ', '.join(funcs[fname]))
    print()
