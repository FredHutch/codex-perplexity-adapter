"""Build a separate local-only AppKit bundle for the qualified anchor strategy."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT.parents[1]
BUILD = ROOT / 'build/anchor-app3'
# Desktop file-provider metadata can be recreated during signing. Keep this
# local-only runnable bundle in a separate temporary staging area.
APP = Path('/private/tmp/codex-perplexity-anchor-test3/Codex Perplexity Adapter Anchor Test 3.app')


def run(*args, **options):
    subprocess.run([str(arg) for arg in args], check=True, **options)


def build():
    BUILD.mkdir(parents=True, exist_ok=True)
    pyinstaller = ORIGINAL / 'build/resize-build-arm64/bin/pyinstaller'
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(BUILD / 'pyinstaller-config'),
               CLANG_MODULE_CACHE_PATH=str(BUILD / 'clang-cache'))
    run(pyinstaller, '--noconfirm', '--clean', '--onefile', '--name', 'adapter-server',
        '--distpath', BUILD / 'server-dist', '--workpath', BUILD / 'server-work',
        '--specpath', BUILD, '--paths', ROOT, '--collect-all', 'uvicorn',
        '--collect-all', 'fastapi', ROOT / 'research/anchor_server_entry.py', env=env)
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / 'Contents'
    (contents / 'MacOS').mkdir(parents=True)
    (contents / 'Resources').mkdir()
    source = (ROOT / 'macos/AdapterLauncher.m').read_text()
    source = source.replace('return [NSBundle.mainBundle.infoDictionary[@"AdapterTestCandidate"] boolValue] ? @"Codex Perplexity Adapter Test" : @"Codex Perplexity Adapter";',
                            'return @"Codex Perplexity Adapter Anchor Test 3";')
    source = source.replace('@"Codex–Perplexity Adapter Test"', '@"Codex–Perplexity Adapter Anchor Test 3"')
    source = source.replace('Version %@ (test candidate rc1)', 'Version %@ (test build 3; earlier-answer continuation)')
    source = source.replace('[self.window makeFirstResponder:self.keyField];\n}', '''[self.window makeFirstResponder:self.keyField];
    [NSUserDefaults.standardUserDefaults registerDefaults:@{@"ResizeImagesOnLimit": @YES}];
    self.resizeCheckbox.state = [NSUserDefaults.standardUserDefaults boolForKey:@"ResizeImagesOnLimit"] ? NSControlStateValueOn : NSControlStateValueOff;
}''', 1)
    launcher = BUILD / 'AnchorLauncher.m'
    launcher.write_text(source)
    run('clang', '-arch', 'arm64', '-O2', '-fobjc-arc', '-mmacosx-version-min=13.0',
        '-framework', 'AppKit', launcher, '-o', contents / 'MacOS/CodexPerplexityAdapter', env=env)
    shutil.copy2(BUILD / 'server-dist/adapter-server', contents / 'Resources/adapter-server')
    shutil.copy2(ORIGINAL / 'build/macos/AppIcon.icns', contents / 'Resources/AppIcon.icns')
    info = plistlib.loads((ROOT / 'macos/Info.plist').read_bytes())
    info.update(CFBundleIdentifier='com.local.codex-perplexity-adapter.anchor-test3',
                CFBundleName='Codex Perplexity Adapter Anchor Test 3',
                CFBundleDisplayName='Codex Perplexity Adapter Anchor Test 3',
                CFBundleVersion='0.1.15.3', AdapterTestPort=4000, AdapterContinuationStrategy='earlier-answer')
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    run('xattr', '-cr', APP)
    identity = 'Developer ID Application: Andrew Portuguese (SFC29VVF27)'
    run('codesign', '--force', '--options', 'runtime', '--timestamp', '--entitlements',
        ROOT / 'macos/entitlements.plist', '--sign', identity, contents / 'Resources/adapter-server')
    run('codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity,
        contents / 'MacOS/CodexPerplexityAdapter')
    run('codesign', '--force', '--options', 'runtime', '--timestamp', '--sign', identity, APP)
    run('codesign', '--verify', '--deep', '--strict', APP)
    print(APP, flush=True)


if __name__ == '__main__':
    build()
