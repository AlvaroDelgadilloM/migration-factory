"""Maven invocations with pinned plugin versions and a controlled settings.xml (mirror of everything)."""
import re
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from ..analysis import xmlparse
from ..config import get_settings
from . import runner

HELP_PLUGIN = 'org.apache.maven.plugins:maven-help-plugin:3.5.1'
COORD = re.compile(r'^[\w.\-]+:[\w.\-]+:\d[\w.\-]*$')
RECIPE = re.compile(r'^[A-Za-z][\w.]{2,200}$')


def settings_file(ctx) -> Path:
    """No credentials; every repository request goes to the approved mirror."""
    s = get_settings()
    p = ctx.workdir / 'meta' / 'settings.xml'
    p.parent.mkdir(parents=True, exist_ok=True)
    extra = [r.split('|', 1) for r in s.maven_extra_repos.split(',') if '|' in r] if s.maven_network == 'allowlist' else []
    extra = [(i.strip(), u.strip()) for i, u in extra if re.fullmatch(r'[\w.\-]{1,50}', i.strip()) and u.strip().startswith('https://')]
    # everything else still goes through the approved mirror. With a workspace overlay (doc 17) OpenRewrite must read the
    # internal artifacts from the local repository, and its resolver would mirror that too with '*': external:* there.
    mirror_of = ('external:*' if current_overlay() else '*') + ''.join(f',!{i}' for i, _ in extra)
    repos = ''.join(f'<repository><id>{escape(i)}</id><url>{escape(u)}</url><snapshots><enabled>false</enabled></snapshots></repository>'
                    for i, u in extra)
    profile = (f'<profiles><profile><id>mf-extra</id><repositories>{repos}</repositories></profile></profiles>'
               f'<activeProfiles><activeProfile>mf-extra</activeProfile></activeProfiles>') if extra else ''
    p.write_text(f'<settings><interactiveMode>false</interactiveMode><mirrors><mirror><id>mf-approved</id>'
                 f'<mirrorOf>{escape(mirror_of)}</mirrorOf><url>{escape(s.maven_mirror_url)}</url></mirror></mirrors>{profile}</settings>')
    return p


# Doc 17 workspaces: internal artifacts (legacy or migrated, same GAV) live in an isolated local repository chained in front
# of the shared cache (Maven >= 3.9 maven.repo.local.tail), so nothing is installed into the shared repository.
# Per thread (doc 20): projects of the same wave can migrate in parallel, each with its own baseline/candidate overlay.
_overlay = threading.local()
_parallel = {'on': False}  # parallel workspace migration: Maven's file locks protect the shared overlays and cache


def use_local_overlay(head: Path | None):
    _overlay.head = head


def current_overlay() -> Path | None:
    return getattr(_overlay, 'head', None)


def set_parallel(on: bool):
    _parallel['on'] = on


def guard(cwd) -> str | None:
    """Maven Execution Guard (doc 20 §14, doc 21 §8/§15): only a ProjectContext with a readable pom.xml is built.
    A directory without pom.xml that contains Maven projects is a workspace root: that is an orchestration error,
    never a source-code error (the workspace build is NOT_APPLICABLE, see mf.build.build_target_resolver)."""
    from ..build.build_target_resolver import for_project
    target = for_project(Path(cwd))
    pom = Path(cwd) / 'pom.xml'
    if not target.buildable:
        if target.reason == 'MISSING_CANDIDATE_POM' and (any(Path(cwd).glob('*/pom.xml')) or any(Path(cwd).glob('*/*/pom.xml'))):
            return f'ORCHESTRATION_ERROR reason=MAVEN_EXECUTED_ON_NON_PROJECT_ROOT: {cwd} es la raíz de un workspace sin POM agregador'
        return f'{target.reason}: no existe {pom if target.reason == "MISSING_CANDIDATE_POM" else cwd}'
    try:
        pom.read_bytes()
    except OSError as e:
        return f'MISSING_CANDIDATE_POM: pom.xml ilegible ({type(e).__name__})'
    return None


def mvn(ctx, cwd, goals, name, timeout=None):
    s = get_settings()
    problem = guard(cwd)
    if problem:  # never start Maven without its project: the error is configuration, not compilation
        log = ctx.workdir / 'logs' / f'{name}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f'[ERROR] {problem}\n' + ('' if problem.startswith('ORCHESTRATION_ERROR')
                                                    else '[ERROR] CANDIDATE_BUILD_CONFIGURATION_ERROR reason=MAVEN_PROJECT_NOT_FOUND\n'))
        return runner.Result(1, 0, log)
    head = current_overlay()
    repo = [f'-Dmaven.repo.local={head}', f'-Dmaven.repo.local.tail={s.maven_repo_local}',
            '-Dmaven.repo.local.tail.ignoreAvailability=true'] if head else [f'-Dmaven.repo.local={s.maven_repo_local}']
    if _parallel['on'] or s.maven_file_locks:  # concurrent Maven processes (parallel wave, or two workers) share repositories
        repo += ['-Daether.syncContext.named.factory=file-lock', '-Daether.syncContext.named.nameMapper=file-gav']
    args = ['mvn', '-B', '-ntp', '-e', '-Dstyle.color=never', '-s', str(settings_file(ctx)), '-f', str(Path(cwd) / 'pom.xml')] + repo \
        + (['-o'] if s.maven_network == 'offline' else []) + list(goals)
    from ..preflight import jansi_tmp  # Jansi native lib must not depend on /tmp being exec-mounted
    home = ctx.workdir / 'home'  # JGit (OpenRewrite) writes ~/.config: keep it in the job workspace, not the read-only image
    home.mkdir(parents=True, exist_ok=True)
    opts = f'-Djansi.tmpdir={jansi_tmp(ctx.workdir)} -Djansi.force=false -Duser.home={home} {s.maven_opts}'
    return runner.run(args, cwd, ctx, name=name, timeout=timeout or s.step_timeout_seconds, env={'MAVEN_OPTS': opts})


def effective_pom(ctx, repo) -> tuple[runner.Result, Path]:
    out = ctx.workdir / 'meta' / 'effective-pom.xml'
    out.parent.mkdir(parents=True, exist_ok=True)
    res = mvn(ctx, repo, [f'{HELP_PLUGIN}:effective-pom', f'-Doutput={out}'], 'maven-effective-pom')
    return res, out


def parse_effective(path: Path) -> dict:
    """{(groupId, artifactId): {'java':..., 'deps': {(g,a): version}, 'packaging':...}}"""
    root = xmlparse.parse(path.read_text(encoding='utf-8'))
    projects = [root] if root.tag == 'project' else root.find('project')
    out = {}
    for p in projects:
        props = {c.tag: c.text.strip() for c in (p.first('properties').children if p.first('properties') else [])}
        java = None
        for plugin in p.find('build/plugins/plugin'):
            if plugin.findtext('artifactId') == 'maven-compiler-plugin':
                java = plugin.findtext('configuration/release') or plugin.findtext('configuration/target') or plugin.findtext('configuration/source')
        java = java or props.get('maven.compiler.release') or props.get('maven.compiler.target') or props.get('maven.compiler.source')
        deps = {(d.findtext('groupId'), d.findtext('artifactId')): d.findtext('version') for d in p.find('dependencies/dependency')}
        out[(p.findtext('groupId'), p.findtext('artifactId'))] = {'java': java, 'deps': deps, 'packaging': p.findtext('packaging', 'jar')}
    return out


def rewrite(ctx, repo, goal, *, plugin_version, artifacts, active, config: Path, name):
    if goal not in ('dryRun', 'run'):
        raise ValueError('goal')
    if not re.fullmatch(r'\d+\.\d+\.\d+', plugin_version) or not all(COORD.match(a) for a in artifacts) \
            or not all(RECIPE.match(r) for r in active):
        raise ValueError('Coordenadas o recetas no fijadas/validas')
    if any(a.endswith((':LATEST', ':RELEASE')) for a in artifacts):
        raise ValueError('LATEST/RELEASE no permitidos')
    # *NoFork goals: no compile lifecycle before the recipes, so a step never depends on intermediate states compiling
    goal_name = {'run': 'runNoFork', 'dryRun': 'dryRunNoFork'}[goal]
    return mvn(ctx, repo, [f'org.openrewrite.maven:rewrite-maven-plugin:{plugin_version}:{goal_name}',
                           f'-Drewrite.activeRecipes={",".join(active)}',
                           f'-Drewrite.recipeArtifactCoordinates={",".join(artifacts)}',
                           f'-Drewrite.configLocation={config}', '-Drewrite.exportDatatables=false',
                           '-Drewrite.failOnDryRunResults=false'], name)


def surefire_summary(repo: Path) -> dict | None:
    totals = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    found = False
    for f in repo.glob('**/target/surefire-reports/TEST-*.xml'):
        try:
            root = xmlparse.parse(f.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            continue
        found = True
        for k in totals:
            try:
                totals[k] += int(root.attrib.get(k, 0))
            except ValueError:
                pass
    return totals if found else None


def collect_patches(repo: Path) -> str:
    return '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in sorted(repo.glob('**/target/rewrite/rewrite.patch')))


def split_patch(patch: str):
    """Split a unified patch (rewrite dryRun output) into per-file entries with recipe attribution."""
    files, cur = [], None
    for line in patch.splitlines(keepends=True):
        if line.startswith('diff --git '):
            m = re.match(r'diff --git a/(.+?) b/(.+)$', line.rstrip('\n'))
            cur = {'path': m[2] if m else line[11:].strip(), 'lines': [line], 'recipes': set(), 'add': 0, 'del': 0, 'kind': 'modified'}
            files.append(cur)
            continue
        if cur is None:
            continue
        cur['lines'].append(line)
        if line.startswith('new file mode'):
            cur['kind'] = 'added'
        elif line.startswith('deleted file mode'):
            cur['kind'] = 'deleted'
        elif line.startswith('@@'):
            tail = line.split('@@')[-1].strip() if line.count('@@') >= 2 else ''
            cur['recipes'] |= {r for r in re.split(r'[,\s]+', tail) if RECIPE.match(r) and '.' in r}
        elif line.startswith('+') and not line.startswith('+++'):
            cur['add'] += 1
        elif line.startswith('-') and not line.startswith('---'):
            cur['del'] += 1
    return [(f['path'], f['kind'], f['add'], f['del'], ''.join(f['lines'])[:500_000], sorted(f['recipes'])) for f in files]
