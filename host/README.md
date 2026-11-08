# Isolated build host

The controller never compiles public C++. `verify` and `review recheck` require
`IBLIS_RUNNER_CONFIG`; missing Docker, missing inputs, changed hashes or a bad
runner response fail closed. Infrastructure failures go to review without a
contributor strike. Every item has its own error boundary. The service default is
`https://iblis.captainlegbreak.workers.dev`.

## Provisioning (owner machine)

1. Enable Docker's **Linux** engine. Use a dedicated local Docker daemon/VM with
   no other sensitive workloads. The adapter uses the default local endpoint,
   never a remote context. Docker Desktop/VM and host kernel must be maintained;
   this is an OS container boundary, not a claim to withstand kernel exploits.
2. Review `Dockerfile`, select an audited Debian bookworm image by digest, and
   build locally: `docker build --build-arg BASE_IMAGE=debian@sha256:<digest>
   -t iblis-runner -f host/Dockerfile host`. Building downloads OS packages only.
   Set `container_image` to the full ID from `docker image inspect iblis-runner`.
   Runtime uses that immutable local ID and `--pull=never`.
3. Supply your legally obtained, authenticated Game.exe and MSVC 2005 SP1 toolchain
   locally. This project does not download or license either. Copy
   `runner.example.json` outside the repository. It already lists all twelve pinned compiler files with their
   SHA-256 (the same set `kit/kit.json` pins) and the 1.14d game hash; set the paths and the image ID.
   Set `game_sha256` to the private repository's `config.json` image hash.
   The compiler hash must also match its configured toolchain prefix.
4. Set `IBLIS_RUNNER_CONFIG` to this file, plus `IBLIS_HOST_KEY` and optionally
   `IBLIS_URL`. Never commit settings, inputs or credentials. Test with known
   matching and nonmatching fixtures using your actual image/toolchain before
   enabling intake or unattended host polling. Docker CLI presence alone does
   not establish readiness. No actual game/compiler run was validated here.

Each launch rehashes the inputs and the copied snapshot. Only allowlisted compiler
files, Game.exe, the public checker/headers, the entry point and one JSON request
are copied into a fresh temporary directory. That directory is the sole read-only
bind mount. The container gets no private repository, host home, Docker socket,
controller key or inherited controller environment. Its root is read-only, network
is disabled, capabilities are dropped, no-new-privileges is set, and it runs as
UID 65532. Docker's default seccomp profile remains enabled. Limits: 1 CPU,
512 MiB memory with no additional swap, 64 processes, 128 descriptors, 16 MiB per
file, bounded tmpfs volumes, 120 seconds per request and 12 MiB response output.
The controller forcibly removes the container on completion, timeout or failure.
The Wine prefix exists only in the disposable tmpfs. Do not relax the boundary to
make a toolchain run; fix the reviewed runtime image or manifest instead.

## Protocol

The read-only `/input/request.json` uses `protocol: 1`, `va`, `length`, `flags`,
`source`, `game_sha256`, `cl`, and `compiler_files`. `runner_entry.py` validates
hashes, invokes the shared `compile_and_compare` inside the container, and writes
one JSON object to stdout: `{protocol:1,result:{...},object_b64:"..."}`. Object
bytes are returned only for matches. The controller validates source and object
hashes and never loads or executes the returned object. Runtime/container/COFF
parser problems are review cases. Compiler, image comparison and runtime diagnostics stay in private local
verdict records; raw diagnostics are never sent to the service.

## Review and import

```
python host/host.py review list --repo PRIVATE_REPO
python host/host.py review recheck --id 123 --repo PRIVATE_REPO
python host/host.py review adjudicate --id 123 --verdict failed --note "reason" --repo PRIVATE_REPO
python host/host.py verify --once --repo PRIVATE_REPO
python host/host.py integrate --repo PRIVATE_REPO
```

Review listing traverses `/api/v1/host/review` cursor pages; `--id` uses its exact
ID filter, so early unresolved reviews cannot hide later cases. Recheck uses the same mandatory isolation
as ordinary verification. Adjudication accepts `verified` or `failed` with an
owner note. A manual acceptance does not invent a successful compiler result or
produce import evidence; use recheck for an importable match.

Import requires a locally recorded match acknowledged by the service, intact
source/object hashes and complete verifier metadata. Source line endings are
normalized to LF before compilation; the original text is retained in the private
verdict record. JSON artifacts are written as LF bytes before manifest hashing. It takes the private tools'
shared `d2match.lock`, imports only a uniquely **open** address, and refuses any
existing submission directory, object drop, claim, parked item or match. It emits
`queue/submitted/<VA>.iblis-<github_id>.json`, `candidate.cpp`, `COMPARE.json`,
`attempts.json` (including the actual compiler argv list) and
`SUBMISSION_MANIFEST.sha256`. Attribution and result identity
are preserved in `IBLIS.json`; notes travel separately. This matches the inspected
private `tools/submit.py` and `tools/verifier_adapt.py` contract (2026-11-08).
Objects stay in `work/host/objects/<VA>/candidate.obj`, unless the repository
explicitly enables `share_objects`. Integration does not run Git add/commit/push;
review these private queue changes and refresh the private grid before committing.
An incomplete import or collision is deferred for owner reconciliation, never
overwritten. Do not publish private source, compiler files, objects or game bytes.

The private verifier adapter requires that local object drop to prepare its
artifacts. **Do not replay compilation or parse contributor objects using the old
unsandboxed owner verifier.** Run any downstream verifier/adapter pipeline in a
separate equally restricted container with only selected submission artifacts and
required game/compiler inputs; its external `vcheck.py` / `vbytes.py` were not
available here and have not been validated. Until that pipeline is isolated and
end-to-end checked with the owner's files, keep unattended integration disabled.
