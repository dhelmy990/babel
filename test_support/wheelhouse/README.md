# Offline build wheelhouse

These wheels come from the PyPI releases for
[setuptools 75.8.0](https://pypi.org/project/setuptools/75.8.0/) and
[wheel 0.45.1](https://pypi.org/project/wheel/0.45.1/). From the repository
root, regenerate the pinned files with:

```sh
python3 -m pip download --disable-pip-version-check --no-deps \
  --only-binary=:all: --index-url https://pypi.org/simple \
  --dest test_support/wheelhouse setuptools==75.8.0 wheel==0.45.1
sha256sum \
  test_support/wheelhouse/setuptools-75.8.0-py3-none-any.whl \
  test_support/wheelhouse/wheel-0.45.1-py3-none-any.whl
```

Expected SHA-256 values:

```text
e3982f444617239225d675215d51f6ba05f845d4eec313da4418fdbb56fb27e3  setuptools-75.8.0-py3-none-any.whl
708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248  wheel-0.45.1-py3-none-any.whl
```

`test_support/build-requirements.lock` enforces these hashes during offline
installation. Verify the wheel files and both package build-system pins with:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest \
  training/tests/test_build_tooling.py -v
```
