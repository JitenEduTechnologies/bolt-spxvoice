# Provider secret storage

How model-provider API keys (Gemini, Deepgram, DeepSeek, Cartesia, ...) are
stored, protected, backed up, rotated and recovered.

## Storage model

| Where | What is stored | Protection |
|---|---|---|
| `user_configurations.configuration` (JSON) | `api_key`, `aws_secret_key`, `credentials` inside `llm/tts/stt/embeddings/realtime` | Fernet envelope `enc:v1:<key_id>:<payload>`, plaintext inside is `user:<id>` + `0x1f` + secret (owner-bound) |
| `workflows.workflow_configurations`, `workflow_definitions.workflow_configurations` | **No provider secrets.** Legacy `model_overrides.*.api_key` values are encrypted in place by the maintenance script and decrypted at run time | Same envelope (context `workflow-override`); new plaintext values are rejected by the route and by `save_workflow_draft` |
| API responses | Masked (`****...abcd`) | Display only. Masking is not encryption |
| Logs, errors, health | Never contain keys, ciphertext or key fragments | Content-free error messages; pydantic errors are logged without input values |
| Workflow node secrets (`qa_api_key`, `tuner_api_key`, any integration `sensitive_fields`) in `workflows.workflow_definition` and `workflow_definitions.workflow_json` | Fernet envelope, context `workflow-node:org:<organization_id>` (organization-bound) | Sealed in `WorkflowClient.create_workflow` / `save_workflow_draft` (all writers); placeholder or foreign envelope rejected (400); decrypted only by the QA LLM config and tuner delivery; legacy plaintext encrypted by the maintenance script |

### Provider endpoints (key exfiltration guard)

A stored key is only sent to the provider's official host (HTTPS, default
port; Azure/ElevenLabs official subdomains) or to an endpoint the deployment
operator approved in `SPX_ADMIN_PROVIDER_ENDPOINTS`
(`provider=https://host[:port][/path],...`). Approved endpoints are SSRF-checked
(literal and resolved addresses: no loopback, private, link-local, reserved,
multicast or metadata) unless `SPX_TRUSTED_LOCAL_ENDPOINTS=true`. Workflow
`model_overrides` run with the workflow owner's key and may only name official
endpoints. Checks run on save (user configuration PUT, workflow save, DB draft
guard) and again immediately before a client is built (Pipecat factory, LiveKit
worker). Keyless sections (e.g. a local Speaches server) are not restricted.
Cross-origin redirects drop the Authorization header in httpx-based clients.
Residual: DNS rebinding between the check and the SDK's own connection is not
pinned for operator-approved custom hosts.

Owner binding applies to user-configuration secrets only. Legacy workflow
override envelopes use a fixed context and are not owner-bound; the API only
accepts an override envelope that decrypts to the value already stored in that
slot.

The acceptance start refuses to generate a new key if ANY text/json column in
the database holds an `enc:v1:` value, and refuses to decide (aborts) if that
check cannot run.

Encryption needs a key in `TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS` (the variable
name is historical; it is shared with telephony credentials). Set
`REQUIRE_CONFIG_ENCRYPTION=true` so the API refuses to store a provider key when
no encryption key is configured; without it, the API stores plaintext and logs a
warning once (legacy behaviour kept for existing deployments).

Reading fails closed: a missing/wrong key, tampered or malformed ciphertext, or
a ciphertext that belongs to another owner returns HTTP 503 with a content-free
message and, for calls, a recorded startup failure. The system never falls back
to "no key configured".

## Acceptance stack key file

* Location: `%LOCALAPPDATA%\SPXVoiceAccept\config-encryption.key` (override with
  `SPX_ACCEPT_KEY_DIR`). It is **outside the candidate tree**, so packaging,
  manifests, evidence bundles and copies of the candidate cannot include it.
* Created atomically (empty temp file -> owner-only ACL -> content -> move), with
  an owner-only ACL on the directory and the file; a loosened ACL is repaired on
  every start.
* Never regenerated while the database contains `enc:v1:` values: if the file is
  missing the start aborts with instructions. A corrupt file also aborts.
* Never print, copy into reports, attach to tickets, or include it in evidence.
  `.gitignore`/`.dockerignore` exclude `*.key` and `accept/run/` as a backstop.

## Backup and recovery

1. Back up the key file to an offline, access-controlled location **separate from
   database backups**. A database backup alone contains only ciphertext; the key
   alone contains no data. Keeping them apart is the point.
2. Recovery: restore the file to the key directory (ACL is re-applied on the next
   start) and start normally.
3. If the key is lost, saved provider keys are unrecoverable. Re-enter them in
   the dashboard after clearing the encrypted rows (acceptance stack: reset the
   database). The start script will not do this silently.

## Rotation

1. Generate a new key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Make the file contain `k2:<new>,k1:<old>` (new key **first**; the first key
   encrypts, every listed key decrypts). A bare existing key is id `k1`.
3. Start the stack, then run
   `python -m api.scripts.config_secrets_maintenance` (dry run) and
   `... --apply`. It re-encrypts every user and workflow-override secret onto the
   primary key and verifies each envelope decrypts. Output is counts only.
4. A second dry run must report 0 changes.
5. **Telephony credentials share this key and are NOT re-encrypted by the
   maintenance script.** Before removing `k1`, re-save every telephony/carrier
   configuration through its own UI/API (which re-encrypts with the primary key),
   or keep `k1` in the list indefinitely. Removing `k1` while any telephony
   credential still carries `enc:v1:k1:` makes it unreadable.
6. Only then remove `k1` from the file and restart. Keep the old key offline until
   a restore test of the new backup passes.

The same script encrypts any legacy plaintext values; the acceptance start runs
it automatically after migrations.

## Production requirements

The acceptance key file is a development convenience, not a production design.
Production must:

* Hold the key in an external secret manager or KMS (AWS KMS/Secrets Manager,
  GCP KMS, Azure Key Vault, HashiCorp Vault), injected at process start; never in
  the image, the repo, `.env` files or compose files.
* Prefer envelope encryption with a KMS-managed key-encryption key so the master
  key never leaves the KMS, with audit logging of decrypt calls.
* Set `REQUIRE_CONFIG_ENCRYPTION=true` and alert on decryption failures.
* Restrict database backup access separately from key access; test restores.
* Rotate on a schedule and on any suspected exposure, using the procedure above.

## Known gaps (tracked, not fixed here)

* Node secrets in workflow exports produced outside the masked API responses
  (e.g. raw DB dumps) are ciphertext, not plaintext; they are only usable with the
  key and within the same organization.
* Concurrent dashboard saves are last-write-wins (no optimistic locking); each save
  writes a complete, valid, encrypted configuration.
* Other tenants' health: `provider_status` on `/api/v1/health` is a deployment-level
  aggregate and is only meaningful for single-owner deployments.


## Known residuals (accepted, documented)

- **Org-less workflows share one envelope context.** A workflow with no
  `organization_id` seals node secrets under `workflow-node:org:None`, so they
  are encrypted but not tenant-isolated from other org-less workflows. Every
  real deployment attaches workflows to an organization.
- **Blocking DNS lookup.** The endpoint SSRF check calls `socket.getaddrinfo`
  synchronously when validating an operator-approved custom host. A slow
  resolver can briefly stall the event loop; it only runs for custom hosts
  (built-in provider hosts skip resolution).
- **Redirects.** httpx drops `Authorization` on cross-origin redirects, but
  provider-specific key headers (`xi-api-key`, `x-goog-api-key`, `api-key`) are
  not stripped. Only official or operator-approved hosts can receive a key, so
  this matters only if such a host itself redirects elsewhere.
- **Plaintext when no key is configured.** Without an encryption key and
  without `REQUIRE_CONFIG_ENCRYPTION=true`, node secrets are stored in
  plaintext; a warning is logged (the secret is never logged). Production and
  the acceptance stack set `REQUIRE_CONFIG_ENCRYPTION=true`.
- **CGNAT/NAT64/6to4.** Resolved addresses in 100.64.0.0/10, and NAT64
  (64:ff9b::/96) / 6to4 (2002::/16) addresses embedding a private IPv4, are
  rejected like other internal addresses.
- Embeddings clients are endpoint-checked at construction (same policy as LLM).
