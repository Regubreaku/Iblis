"""Trusted-local runner: compiles submissions directly on the host machine, without the Docker boundary.

For an invite-only soft launch, or for any contributor on a disposable isolated machine. Opt in with both:
  IBLIS_RUNNER=trusted-local
  IBLIS_TRUSTED_GITHUB=login1,login2,12345     GitHub logins or numeric ids whose work this host compiles
Everyone else's submissions are not compiled: they go to review, to be rechecked later by the isolated Docker runner.
IBLIS_TRUSTED_GITHUB=* compiles everyone's work. Use it only where the machine itself is the isolation: a disposable
cloud container or VM with nothing else on it, never a personal computer.
The compiler, game and settings are the kit's own: run `python kit/iblis_check.py setup --game ... --fetch` and
`selftest` on this machine first. The kit's compile step bounds time and output, but it is not a sandbox: on a
personal computer, list trusted accounts, or use IBLIS_RUNNER_CONFIG (the Docker runner) for public work.
"""
import hashlib, importlib.util, json, os, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def _kit():
    spec = importlib.util.spec_from_file_location("iblis_check_host", os.path.join(HERE, "..", "kit", "iblis_check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def trusted_set(text):
    return {x.strip().lower() for x in str(text or "").split(",") if x.strip()}


class TrustedLocalRunner:
    kind = "trusted-local"

    def __init__(self, image_sha256, cl_sha256=None, trusted=None, settings_path=None):
        self.trusted = trusted_set(trusted if trusted is not None else os.environ.get("IBLIS_TRUSTED_GITHUB"))
        if not self.trusted:
            raise RuntimeError("IBLIS_TRUSTED_GITHUB must list the invited GitHub logins or ids for the trusted-local runner")
        self.kit = _kit()
        path = settings_path or os.path.join(os.path.expanduser("~"), ".iblis", "settings.json")
        if not os.path.exists(path):
            raise RuntimeError("no kit settings at %s: run kit/iblis_check.py setup and selftest on this machine" % path)
        self.settings = json.load(open(path))
        if self.kit.sha256_file(self.settings["image"]) != image_sha256:
            raise RuntimeError("Game.exe in the kit settings does not hash to the repository's image")
        if cl_sha256 and self.kit.sha256_file(self.settings["cl"]) != cl_sha256:
            raise RuntimeError("cl.exe in the kit settings does not hash to the repository's pinned compiler")

    def allows(self, item):
        if "*" in self.trusted:
            return True
        return str(item.get("login") or "").lower() in self.trusted or str(item.get("github_id") or "") in self.trusted

    def compile(self, va, length, flags, source):
        wd = tempfile.mkdtemp(prefix="iblis-local-")
        try:
            res = self.kit.compile_and_compare(self.settings, va, length, list(flags), source, wd)
            obj = os.path.join(wd, "candidate.obj")
            if res.get("status") in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL") and os.path.exists(obj):
                res["object_bytes"] = open(obj, "rb").read()
                res["object_sha256"] = hashlib.sha256(res["object_bytes"]).hexdigest()
            res["runner"] = self.kind
            return res
        finally:
            shutil.rmtree(wd, ignore_errors=True)
