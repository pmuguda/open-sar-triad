"""
A linter for the workflow files, run as tests.

GitHub does not check most of what goes wrong in a workflow. A step id spelled
differently where it is defined and where it is read evaluates to an empty
string, not an error; so does an output nobody writes; a script that was
renamed fails only when the step finally runs, which for the weekly ingest is
next Monday. Every check here is one of those: something that is wrong on the
page today and would only be found out by a red run later.

These read the YAML. What the steps actually do when run is covered in
test_workflow_scripts.py.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
WF = ROOT / ".github" / "workflows"
FILES = sorted(p.name for p in WF.glob("*.yml"))


def load(name):
    return yaml.safe_load((WF / name).read_text())


def triggers(doc):
    # PyYAML reads a bare `on:` key as the boolean True.
    t = doc.get("on", doc.get(True, {}))
    return t if isinstance(t, dict) else {k: None for k in (t if isinstance(t, list) else [t])}


def jobs(name):
    return load(name)["jobs"].items()


def steps(job):
    return job.get("steps") or []


def text_of(step):
    """Everything in a step an expression can appear in."""
    parts = [str(step.get("if", "")), str(step.get("run", ""))]
    parts += [str(v) for v in (step.get("with") or {}).values()]
    parts += [str(v) for v in (step.get("env") or {}).values()]
    return "\n".join(parts)


def job_text(job):
    own = [str(job.get("if", "")), str(job.get("environment", ""))]
    own += [str(v) for v in (job.get("env") or {}).values()]
    return "\n".join(own + [text_of(s) for s in steps(job)])


def test_there_are_workflows_to_lint():
    assert len(FILES) >= 6, FILES


@pytest.mark.parametrize("name", FILES)
def test_every_workflow_parses_and_has_jobs(name):
    doc = load(name)
    assert doc.get("name"), f"{name} has no name"
    assert doc.get("jobs"), f"{name} has no jobs"
    assert triggers(doc), f"{name} has no triggers"


# --------------------------------------------------------------------------- #
# References that silently evaluate to nothing when they are wrong
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", FILES)
def test_every_step_reference_names_a_real_step(name):
    """`steps.depoy.outcome` is not an error. It is the empty string, and the
    condition it sits in quietly takes the other branch for ever."""
    for job_id, job in jobs(name):
        ids = {s["id"] for s in steps(job) if "id" in s}
        used = set(re.findall(r"\bsteps\.([A-Za-z0-9_-]+)\.", job_text(job)))
        assert used <= ids, f"{name}:{job_id} reads steps {sorted(used - ids)} that do not exist"


@pytest.mark.parametrize("name", FILES)
def test_a_step_is_only_read_after_it_has_run(name):
    """A step's outcome is empty until the step has had its turn."""
    for job_id, job in jobs(name):
        seen = set()
        for s in steps(job):
            used = set(re.findall(r"\bsteps\.([A-Za-z0-9_-]+)\.", text_of(s)))
            early = used - seen - {s.get("id")}
            defined_later = early & {x.get("id") for x in steps(job)}
            assert not defined_later, \
                f"{name}:{job_id} step {s.get('name') or s.get('id')!r} reads {sorted(defined_later)} before they run"
            if "id" in s:
                seen.add(s["id"])


@pytest.mark.parametrize("name", FILES)
def test_every_output_that_is_read_is_written(name):
    """`steps.commit.outputs.pushd` would make the deploy step never run."""
    for job_id, job in jobs(name):
        by_id = {s["id"]: s for s in steps(job) if "id" in s}
        for sid, out in re.findall(r"\bsteps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)", job_text(job)):
            step = by_id[sid]
            if "uses" in step:
                continue                      # an action's outputs are its own business
            run = step.get("run", "")
            written = re.search(rf'echo\s+"{re.escape(out)}=.*>>\s*"?\$GITHUB_OUTPUT', run)
            assert written, f"{name}:{job_id} reads steps.{sid}.outputs.{out}, which that step never writes"


@pytest.mark.parametrize("name", FILES)
def test_every_needs_reference_is_declared(name):
    doc = load(name)
    for job_id, job in jobs(name):
        needs = job.get("needs") or []
        needs = [needs] if isinstance(needs, str) else needs
        assert set(needs) <= set(doc["jobs"]), f"{name}:{job_id} needs a job that does not exist"
        used = set(re.findall(r"\bneeds\.([A-Za-z0-9_-]+)\.", job_text(job)))
        assert used <= set(needs), \
            f"{name}:{job_id} reads needs.{sorted(used - set(needs))} without declaring them"


@pytest.mark.parametrize("name", FILES)
def test_every_input_that_is_read_is_declared(name):
    doc = load(name)
    declared = set(((triggers(doc).get("workflow_dispatch") or {}).get("inputs") or {}))
    for job_id, job in jobs(name):
        used = set(re.findall(r"\binputs\.([A-Za-z0-9_-]+)", job_text(job)))
        assert used <= declared, f"{name}:{job_id} reads undeclared inputs {sorted(used - declared)}"


@pytest.mark.parametrize("name", FILES)
def test_every_script_a_step_runs_exists(name):
    """A renamed script fails only when its step finally runs, which for the
    weekly ingest is next Monday."""
    for job_id, job in jobs(name):
        for s in steps(job):
            for path in re.findall(r"\b(scripts/[A-Za-z0-9_./-]+\.(?:py|sh))\b", s.get("run", "")):
                assert (ROOT / path).exists(), f"{name}:{job_id} runs {path}, which does not exist"
            for path in re.findall(r"-r\s+(requirements[A-Za-z0-9_.-]*\.txt)", s.get("run", "")):
                assert (ROOT / path).exists(), f"{name}:{job_id} installs {path}, which does not exist"


# --------------------------------------------------------------------------- #
# One workflow starting another
# --------------------------------------------------------------------------- #
def dispatches():
    """(workflow, job id, job, target workflow, {input: value}) for every
    `gh workflow run`."""
    out = []
    for name in FILES:
        for job_id, job in jobs(name):
            for s in steps(job):
                for m in re.finditer(r"gh workflow run (\S+)([^\n]*)", s.get("run", "")):
                    fields = dict(re.findall(r"-f\s+([A-Za-z0-9_-]+)=(\S+)", m.group(2)))
                    out.append((name, job_id, job, m.group(1), fields))
    return out


def test_something_dispatches():
    """If this is empty the checks below pass by checking nothing."""
    assert len(dispatches()) >= 4


@pytest.mark.parametrize("name, job_id, job, target, fields", dispatches(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_a_dispatch_targets_a_workflow_that_accepts_it(name, job_id, job, target, fields):
    assert (WF / target).exists(), f"{name} dispatches {target}, which does not exist"
    wd = triggers(load(target))
    assert "workflow_dispatch" in wd, f"{name} dispatches {target}, which has no workflow_dispatch"
    declared = set(((wd.get("workflow_dispatch") or {}).get("inputs") or {}))
    assert set(fields) <= declared, \
        f"{name} passes inputs {sorted(set(fields) - declared)} that {target} does not declare"


@pytest.mark.parametrize("name, job_id, job, target, fields", dispatches(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_a_dispatching_job_is_allowed_to_dispatch(name, job_id, job, target, fields):
    """Without actions: write the call is refused, at run time, with a 403."""
    perms = {**(load(name).get("permissions") or {}), **(job.get("permissions") or {})}
    if "permissions" in job:                  # job-level permissions replace, not merge
        perms = job["permissions"]
    assert perms.get("actions") == "write", f"{name}:{job_id} dispatches {target} without actions: write"


@pytest.mark.parametrize("name, job_id, job, target, fields", dispatches(),
                         ids=lambda v: v if isinstance(v, str) else None)
def test_a_dispatching_step_has_a_token(name, job_id, job, target, fields):
    for s in steps(job):
        if "gh workflow run" in s.get("run", ""):
            assert "GH_TOKEN" in (s.get("env") or {}), \
                f"{name}:{job_id} step {s.get('name')!r} calls gh without GH_TOKEN"


# --------------------------------------------------------------------------- #
# Pushing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", FILES)
def test_nothing_pushes_with_a_bare_git_push(name):
    """A bare push loses the race against the other scheduled workflow, or a
    person, and takes the run's work with it."""
    for job_id, job in jobs(name):
        for s in steps(job):
            run = "\n".join(l.split("#", 1)[0] for l in s.get("run", "").splitlines())
            assert not re.search(r"\bgit push\b", run), \
                f"{name}:{job_id} step {s.get('name')!r} uses a bare git push; use scripts/git_push_retry.sh"


def test_the_workflows_that_commit_use_the_retrying_push():
    for name in ("fetch-data.yml", "fetch-usage.yml"):
        assert "scripts/git_push_retry.sh origin main" in (WF / name).read_text(), name


@pytest.mark.parametrize("name", FILES)
def test_a_job_that_pushes_may_write_contents(name):
    for job_id, job in jobs(name):
        if "git_push_retry.sh" not in job_text(job):
            continue
        perms = job.get("permissions") or load(name).get("permissions") or {}
        assert perms.get("contents") == "write", f"{name}:{job_id} pushes without contents: write"


# --------------------------------------------------------------------------- #
# Schedules and action versions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", FILES)
def test_a_scheduled_workflow_can_also_be_run_by_hand(name):
    """Otherwise the only way to retry a failed scheduled run is to wait."""
    t = triggers(load(name))
    if "schedule" in t:
        assert "workflow_dispatch" in t, f"{name} runs on a schedule only"


@pytest.mark.parametrize("name", FILES)
def test_cron_expressions_have_five_fields(name):
    for entry in (triggers(load(name)).get("schedule") or []):
        assert len(entry["cron"].split()) == 5, f"{name}: {entry['cron']!r}"


#: The first major of each action that runs on Node 24. Older majors target
#: Node 20, which GitHub has deprecated: they still work only because the
#: runner forces them onto Node 24, with a warning on every run.
NODE24_FROM = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/upload-artifact": 6,
    "actions/download-artifact": 7,
    "actions/cache": 5,
    "actions/configure-pages": 6,
    "actions/upload-pages-artifact": 5,
    "actions/deploy-pages": 5,
}


def uses():
    out = []
    for name in FILES:
        for job_id, job in jobs(name):
            for s in steps(job):
                if "uses" in s:
                    out.append((name, s["uses"]))
    return sorted(set(out))


@pytest.mark.parametrize("name, ref", uses())
def test_every_action_is_pinned_to_a_version(name, ref):
    assert "@" in ref and ref.split("@", 1)[1], f"{name} uses {ref} with no version"


@pytest.mark.parametrize("name, ref", uses())
def test_no_action_is_on_a_deprecated_runtime(name, ref):
    action, _, version = ref.partition("@")
    if action not in NODE24_FROM:
        return
    m = re.fullmatch(r"v(\d+)", version)
    assert m, f"{name}: {ref} is not pinned to a major version"
    assert int(m.group(1)) >= NODE24_FROM[action], \
        f"{name}: {ref} runs on Node 20; use v{NODE24_FROM[action]} or later"


def test_every_action_in_use_is_known_to_the_runtime_table():
    """A new actions/* action added without an entry would slip past the check."""
    unknown = {r.partition("@")[0] for _, r in uses()
               if r.startswith("actions/") and r.partition("@")[0] not in NODE24_FROM}
    assert not unknown, f"add {sorted(unknown)} to NODE24_FROM"


# --------------------------------------------------------------------------- #
# The deploy's retry, and the site check's repair
# --------------------------------------------------------------------------- #
def step(name, job_id, step_id):
    return next(s for s in load(name)["jobs"][job_id]["steps"] if s.get("id") == step_id)


def test_the_deploy_retries_only_when_the_deployment_failed():
    cond = step("deploy.yml", "deploy", "retry")["if"]
    assert "steps.deployment.outcome == 'failure'" in cond
    assert "!cancelled()" in cond, "a cancelled run must not start another"


def test_the_deploy_does_not_report_while_a_retry_is_on_its_way():
    report = next(s for s in load("deploy.yml")["jobs"]["deploy"]["steps"]
                  if "report_health.py" in s.get("run", ""))
    assert "steps.retry.outputs.retrying != 'true'" in report["if"]


def test_the_retry_is_bounded():
    doc = load("deploy.yml")
    assert int(doc["env"]["MAX_ATTEMPTS"]) in (2, 3, 4), "retry, but not for ever"
    assert "attempt" in triggers(doc)["workflow_dispatch"]["inputs"]


def test_a_newer_deploy_replaces_an_older_one():
    """Two deploys at once would race to publish; the newer must win."""
    c = load("deploy.yml")["concurrency"]
    assert c["group"] == "pages" and c["cancel-in-progress"] is True


def test_the_site_check_compares_against_the_live_tip_of_main():
    """github.sha is fixed when a scheduled run is queued, and GitHub starts
    them hours late, so a commit deployed in between made the site "wrong"."""
    text = (WF / "site-check.yml").read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "git ls-remote origin refs/heads/main" in code
    assert "github.sha" not in code


def test_the_site_check_repairs_before_it_reports():
    job = load("site-check.yml")["jobs"]["check"]
    order = [s.get("id") or ("report" if "report_health.py" in s.get("run", "") else None)
             for s in job["steps"]]
    assert order.index("verify") < order.index("heal") < order.index("reverify") < order.index("report")
    assert step("site-check.yml", "check", "verify").get("continue-on-error") is True, \
        "a failed first comparison must not stop the repair from running"
    assert "steps.verify.outcome == 'failure'" in step("site-check.yml", "check", "heal")["if"]
    assert "gh workflow run deploy.yml" in step("site-check.yml", "check", "heal")["run"]


def test_the_site_check_is_red_when_the_repair_did_not_work():
    """continue-on-error on the first comparison would otherwise leave the run
    green with the site still wrong."""
    last = load("site-check.yml")["jobs"]["check"]["steps"][-1]
    assert "exit 1" in last["run"]
    assert "steps.verify.outcome == 'failure'" in last["if"]
    assert "steps.reverify.outcome != 'success'" in last["if"]


# --------------------------------------------------------------------------- #
# The test workflow itself
# --------------------------------------------------------------------------- #
def test_the_tests_run_on_every_push_to_main():
    """A path filter listing the Python directories meant a change to the
    stylesheet, the page or a workflow, all of which the suite checks, never
    ran it."""
    push = triggers(load("tests.yml"))["push"]
    assert "paths" not in push and "paths-ignore" not in push


def test_ci_fails_on_unexpected_skips_and_names_every_skip():
    run = next(s for s in load("tests.yml")["jobs"]["test"]["steps"] if s.get("name") == "Run tests")
    assert (run.get("env") or {}).get("OST_STRICT_SKIPS") == "1"
    assert "-rs" in run["run"]


def test_ci_installs_what_the_workflow_tests_need():
    assert re.search(r"(?im)^pyyaml", (ROOT / "requirements-dev.txt").read_text())


def test_ci_fetches_the_data_the_terrain_tests_measure():
    text = (WF / "tests.yml").read_text()
    assert "DEM_TEST_POINTS" in text and "data/.dem-cache" in text


# --------------------------------------------------------------------------- #
# Pythons and the runner images they exist on
# --------------------------------------------------------------------------- #
#: The newest Ubuntu image each end-of-life Python was built for. `ubuntu-latest`
#: is whatever GitHub currently means by it, which stopped including these.
LAST_IMAGE_FOR = {"3.8": "ubuntu-24.04", "3.9": "ubuntu-24.04"}


def python_legs():
    """(workflow, job, python version, runner) for every job that sets up Python."""
    out = []
    for name in FILES:
        for job_id, job in jobs(name):
            for s in steps(job):
                if not str(s.get("uses", "")).startswith("actions/setup-python@"):
                    continue
                version = str((s.get("with") or {}).get("python-version", ""))
                runner = str(job.get("runs-on", ""))
                if "matrix." in version or "matrix." in runner:
                    legs = (job.get("strategy") or {}).get("matrix", {}).get("include", [])
                    assert legs, (f"{name}:{job_id} builds its matrix without `include`, so a "
                                  "Python cannot be paired with the runner it needs")
                    for leg in legs:
                        out.append((name, job_id, str(leg.get("python-version", version)),
                                    str(leg.get("os", runner))))
                else:
                    out.append((name, job_id, version, runner))
    return out


def test_the_python_matrix_is_read():
    legs = [l for l in python_legs() if l[0] == "tests.yml" and l[1] == "test"]
    assert {l[2] for l in legs} == {"3.9", "3.11", "3.12"}, legs


@pytest.mark.parametrize("name, job_id, version, runner", python_legs())
def test_an_end_of_life_python_is_pinned_to_an_image_that_has_it(name, job_id, version, runner):
    """Python 3.9 was never built for Ubuntu 26.04. When `ubuntu-latest` moved
    there the 3.9 leg failed in setup-python before any test ran."""
    if version in LAST_IMAGE_FOR:
        assert runner == LAST_IMAGE_FOR[version], \
            f"{name}:{job_id} runs Python {version} on {runner}; it needs {LAST_IMAGE_FOR[version]}"


def test_the_oldest_python_tested_is_the_oldest_the_client_claims():
    """Dropping the 3.9 leg to make CI green would leave `requires-python`
    promising something nothing checks."""
    pyproject = (ROOT / "python" / "pyproject.toml").read_text()
    floor = re.search(r'requires-python\s*=\s*">=\s*([0-9.]+)"', pyproject).group(1)
    tested = {l[2] for l in python_legs() if l[0] == "tests.yml" and l[1] == "test"}
    assert floor in tested, f"the client supports {floor} but CI tests only {sorted(tested)}"
