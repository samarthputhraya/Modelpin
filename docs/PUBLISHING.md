# Publishing Modelpin

Steps that need **your own accounts/tokens** (a coding agent can't do these for you). The
package is already PyPI-ready: `pyproject.toml` has metadata + classifiers, `mp`/`modelpin`
entry points, and `python -m pip wheel . --no-deps` builds a clean wheel.

## 1. PyPI (so `pipx install modelpin` works)

```bash
# one-time: install build + twine
python -m pip install --upgrade build twine

# build sdist + wheel into dist/
rm -rf build dist ./*.egg-info   # a STALE egg-info makes setuptools ship files the current
                                 # pyproject no longer selects -- a false green at release time
python -m build            # or: python -m pip wheel . --no-deps -w dist

# (recommended) smoke-test on TestPyPI first
twine upload --repository testpypi dist/*
pipx install --index-url https://test.pypi.org/simple/ "modelpin[providers]"

# publish to real PyPI (needs a PyPI account + an API token in ~/.pypirc or TWINE_PASSWORD)
twine upload dist/*
```

After publishing, anyone can:

```bash
pipx install "modelpin[providers]"     # isolated CLI install
# or
pip install "modelpin[providers]"
mp --help
```

Bump `version` in `pyproject.toml` **and** `__version__` in `modelpin/__init__.py` for each
release (keep them in sync), tag it (`git tag v0.1.0 && git push --tags`), then re-`build` +
`twine upload`.

## 2. GitHub Action

`action.yml` is at the **repo root** (so it's Marketplace-eligible); detailed docs are in
[`actions/README.md`](../actions/README.md). Consumers reference it as
`samarthputhraya/modelpin@v1`. `modelpin` is on PyPI, so the action's default
`modelpin-spec: modelpin[providers]` works out of the box.

To publish to the **GitHub Marketplace**: push a tag (e.g. `v1`), open the release for that tag
on GitHub, and check "**Publish this Action to the GitHub Marketplace**" (needs the root
`action.yml` ✓ and a unique action name — "Modelpin"; accept the Marketplace terms if prompted).
Maintain a moving `v1` tag (re-point it on each release) for consumers who pin to the major.

## 3. Checklist before the first public release

- [ ] `README.md` quickstart is accurate (install, `mp init/baseline/check`, the Action).
- [x] Every `[project.urls]` value points at a host that exists. **Now executable** —
      `tests/test_report_claims.py::test_every_published_project_url_points_somewhere_that_exists`
      allowlists the hosts, so this checkbox cannot silently go stale again. `[M] 2026-08-27`
      it had been unticked since 2026-06-25 while `Homepage = https://modelpin.dev` — a domain
      with no A record — shipped on the live 0.2.0 PyPI page (MP-126).
- [ ] `LICENSE` present (Apache-2.0 ✓).
- [ ] `version` synced in `pyproject.toml` + `modelpin/__init__.py`.
- [ ] A green CI run of the Action against a real model (dogfood on this repo's examples).
