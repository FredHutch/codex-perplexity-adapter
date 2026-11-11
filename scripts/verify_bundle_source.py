"""Compare frozen Python code with source under the bundle's own Python version."""
from pathlib import Path
import json
import marshal
import sys
import types
from PyInstaller.archive.readers import CArchiveReader


def signature(code):
    def constant(value):
        if isinstance(value,types.CodeType):return signature(value)
        if isinstance(value,tuple):return tuple(constant(x) for x in value)
        if isinstance(value,frozenset):return sorted(repr(constant(x)) for x in value)
        return value
    return (code.co_code,code.co_argcount,code.co_posonlyargcount,code.co_kwonlyargcount,
        code.co_nlocals,code.co_stacksize,code.co_flags,code.co_names,code.co_varnames,
        code.co_freevars,code.co_cellvars,tuple(constant(x) for x in code.co_consts))

root=Path(__file__).resolve().parents[1]
archive=CArchiveReader(sys.argv[1]);pyz=archive.open_embedded_archive('PYZ.pyz')
results={}
for module in ['__init__','app','cli','image_resize','continuation','request_size','status','transform']:
    name='codex_perplexity_adapter'+('.'+module if module!='__init__' else '')
    bundled=pyz.extract(name)
    source=root/'codex_perplexity_adapter'/(module+'.py')
    expected=compile(source.read_bytes(),bundled.co_filename,'exec')
    results[module]=signature(expected)==signature(bundled)
if '--anchor' in sys.argv:
    bundled=pyz.extract('research.tool_continuation')
    expected=compile((root/'research/tool_continuation.py').read_bytes(),bundled.co_filename,'exec')
    results['research.tool_continuation']=signature(expected)==signature(bundled)
    entry=marshal.loads(archive.extract('anchor_server_entry'))
    results['anchor_server_entry']=signature(entry)==signature(compile((root/'research/anchor_server_entry.py').read_bytes(),entry.co_filename,'exec'))
codecs={name: name in archive.toc for name in ('PIL/_imaging.cpython-313-darwin.so',)}
print(json.dumps({'python':sys.version.split()[0],'modules_match':results,'codecs':codecs}))
if not all(results.values()) or not all(codecs.values()):raise SystemExit(1)
