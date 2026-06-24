# ABS Test VM Provisioning Runbook

Use this skill when you need to:
- Spin up a disposable test ABS (AudioBookShelf) instance for MCP pipeline testing
- Create a fresh KVM VM on the `dl380` host cloned from the Arch Linux base image
- Configure ABS via its REST API (no web UI interaction)
- Test the audiobook-ingestion MCP container end-to-end

Do NOT use this for production ABS instances. Do NOT use for VMs on any host other than `dl380`. Do NOT use OpenAudible on the test VM -- MCP requires Libation.

## When to Use

Trigger phrases:
- "test the container in a VM", "end-to-end test", "functional test", "KVM VM", "test ABS"
- "spin up a disposable ABS", "throwaway test environment", "pipeline test in isolation"
- "libationcli inside container", "ABS init endpoint"

Do NOT trigger for: production ABS (`archlinux-audiobookshelf` is a prod VM), bare-metal setups, MCP server code changes, ABS server upgrades.

## Critical Pre-Flight: Base Image Source

The base image name on `dl380` has changed. **Do not assume `archlinux-base-clone` is the right name** -- it may be stale or have a broken guest agent.

```bash
# 1. List actual VMs on dl380 to find the current base
kvm_list_vms(host="dl380")

# 2. Confirm guest agent works on the candidate base BEFORE cloning
kvm_guest_ping(vm_name="<candidate>", host="dl380")
kvm_guest_get_ip(vm_name="<candidate>", host="dl380")
```

If guest agent doesn't respond, the base image's qcow2 has a broken agent channel. The fix is to copy the qcow2 + define a new libvirt domain via `virt-install --import` (not `virt-clone`).

**Verified working base (as of 2026-06-24):** `archlinux` (id=22, running on dl380, IP 192.168.101.126, guest agent responsive).

## VM Creation Patterns

There are TWO valid approaches. Pick based on whether the base VM is shut off or running.

### Pattern A: Source VM is SHUT OFF (clean approach)

```python
# 1. Clone via libvirt (source must be off)
kvm_clone_vm(
    source_vm_name="<base>",
    target_name="<test-vm-name>",
    host="dl380"
)

# 2. Start the clone
kvm_start_vm(vm_name="<test-vm-name>", host="dl380", wait_for_agent=True)
```

**Constraint:** `virt-clone` does NOT accept `--ram` or `--vcpu` arguments. If you need to override memory/CPU, do it AFTER cloning via `virsh setmem` / `virsh setvcpus`, or use Pattern B.

### Pattern B: Source VM is RUNNING (workaround for live base)

This is the pattern I had to use because the rebuilt `archlinux` base was running.

```bash
# On dl380 host:
ssh root@dl380 "cp /var/lib/libvirt/images/base_images/archlinux-base-clone.qcow2 \
                  /var/lib/libvirt/images/base_images/<test-vm-name>.qcow2"

ssh root@dl380 "virt-install --name <test-vm-name> \
                  --memory 4096 --vcpus 2 \
                  --disk /var/lib/libvirt/images/base_images/<test-vm-name>.qcow2,format=qcow2,bus=virtio \
                  --import --os-variant archlinux \
                  --network bridge=br0,model=virtio \
                  --noautoconsole --noreboot"
```

Then via the kvm-manager MCP:

```python
kvm_start_vm(vm_name="<test-vm-name>", host="dl380", wait_for_agent=True)
```

**Why this works:** `virt-clone` refuses to clone a running source. Direct qcow2 copy + `virt-install --import --noreboot` defines the domain without starting it, then `kvm_start_vm` brings it up.

## Network and SSH

After `kvm_start_vm(wait_for_agent=True)` returns successfully:

```python
# Get the IP (returns IPv6 link-local OR IPv4 DHCP -- check both)
ip_info = kvm_guest_get_ip(vm_name="<test-vm-name>", host="dl380")
# Returns dict with "ip_address" key

# Confirm IPv4 via guest_exec (more reliable)
result = guest_exec(
    vm_name="<test-vm-name>",
    command="ip addr show",  # Look for inet 192.168.X.X/YY under enp1s0
    host="dl380"
)
```

**IP convention on dl380:** The Arch base gets DHCP on `192.168.100.0/21` via `br0`. Expected range: `192.168.100.x` to `192.168.103.255`.

### SSH key injection (one-time, per VM)

The base image does not have your SSH key. Inject it before relying on SSH:

```python
kvm_guest_inject_ssh_key(
    vm_name="<test-vm-name>",
    public_key="<your ed25519/rsa public key as a single string>",
    host="dl380",
    username="root"
)
```

Then SSH with strict host key checking off (the base image's host key changes per clone):

```bash
ssh -o StrictHostKeyChecking=no root@<vm-ip> "uname -a"
```

## Docker Installation Inside the VM

The base image is Arch Linux. `pacman` is NOT in the `guest_exec` allowlist -- use SSH.

```bash
ssh root@<vm-ip> "pacman -Sy --noconfirm docker && systemctl enable --now docker"
ssh root@<vm-ip> "systemctl is-active docker"  # Should print: active
ssh root@<vm-ip> "docker info | grep Server"    # Should show Server Version
```

## ABS Container Setup (INSIDE the test VM)

The ABS Docker image is at `ghcr.io/advplyr/audiobookshelf:latest`. It listens on port 80 internally. The test VM's host port 13378 is the conventional choice for ABS (matches your production setup at `kids-audio-books.x86experts.com:13378`).

### Start ABS

```bash
ssh root@<vm-ip> "docker run -d --name abs-test -p 13378:80 \
  -v /tmp/abs-test/config:/config \
  -v /tmp/abs-test/metadata:/metadata \
  -v /tmp/abs-test/audiobooks:/audiobooks \
  ghcr.io/advplyr/audiobookshelf:latest"
```

Health check from the agent host:

```bash
curl -s --retry 10 --retry-delay 2 --retry-connrefused http://<vm-ip>:13378/healthcheck
# Should return: OK
```

### ABS API: Initialization

**CRITICAL:** The init endpoint is `POST /init` (root path), NOT `POST /api/init` and NOT `POST /api/initialize`. The body shape is `{newRoot: {username, password}}`, NOT `{newUser: ...}` or `{root: ...}`.

```bash
# Right way:
curl -s -X POST http://<vm-ip>:13378/init \
  -H "Content-Type: application/json" \
  -d '{"newRoot":{"username":"admin","password":"adminpass123"}}'
# Returns: OK

# WRONG -- crashes the container with TypeError: Cannot read properties of undefined (reading 'username'):
curl -X POST http://<vm-ip>:13378/api/init -d '{"newUser":{"username":"admin",...}}'
```

**What goes wrong if you get it wrong:** ABS throws `TypeError: Cannot read properties of undefined (reading 'username')` because the route handler at `Server.js:434` does `req.body.newRoot.username` and you've passed nothing under `newRoot`. ABS logs the unhandled rejection and keeps running, but the request returns empty and no user is created.

**The route code (verified 2026-06-24, ABS v2.35.1):**

```javascript
// /app/server/Server.js line 341
router.post('/init', (req, res) => {
  if (Database.hasRootUser) {
    Logger.error(`[Server] attempt to init server when server already has a root user`)
    return res.sendStatus(500)
  }
  this.initializeServer(req, res)
})

// /app/server/Server.js line 434
async initializeServer(req, res) {
  const newRoot = req.body.newRoot           // <-- this is what crashes if missing
  const rootUsername = newRoot.username || 'root'
  const rootPash = newRoot.password ? await this.auth.localAuthStrategy.hashPassword(newRoot.password) : ''
  if (!rootPash) Logger.warn(`[Server] Creating root user with no password`)
  await Database.createRootUser(rootUsername, rootPash, this.auth)
  res.sendStatus(200)
}
```

### ABS API: Auth (after init)

```bash
# Get JWT
TOKEN=$(curl -s -X POST http://<vm-ip>:13378/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"adminpass123"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["user"]["token"])')

# Create a library
curl -s -X POST http://<vm-ip>:13378/api/libraries \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"adult","folders":[{"fullPath":"/audiobooks"}],"icon":"book"}'
# Returns: {"id": "<library-uuid>", "name": "adult", ...}
```

**Capture the library ID** -- you'll need it for the MCP container's `libraries.yaml`.

## MCP Container Inside the Test VM

The audiobook-ingestion MCP image bundles Libation + Python + MCP server. The image is published at `ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest`.

### CRITICAL: Libation env var that nobody documents

Libation's CLI inside the container looks for its config at `/config-internal/Settings.json` by default. If you mount Libation config at `/config` (which the standard pattern does), you MUST set `LIBERATION_FILES_DIR` (no, wait -- the var is `LIBATION_FILES_DIR`):

```bash
-e LIBATION_FILES_DIR=/config
```

**Without this env var,** every `libationcli` invocation fails with:

```
Cannot find settings files at /config-internal/Settings.json
Override LibationFiles directory location with '--libationFiles' option or 'LIBATION_FILES_DIR' environment variable.
```

**This is NOT the same as `LIBATION_CONFIG_DIR`** (which the MCP server reads separately for the runtime env file). Two different env vars for two different purposes:

- `LIBATION_CONFIG_DIR` (or `MCP_ENV_FILE`) -- tells the MCP server where its `.env` is
- `LIBATION_FILES_DIR` -- tells the Libation CLI where its own internal config is

### Volume mounts (5 categories)

```bash
docker run -d --name mcp-test \
  -e LIBATION_CLI=/libation/LibationCli \
  -e LIBATION_FILES_DIR=/config \
  -e MCP_ENV_FILE=/app/abs-mcp/.env \
  -v /opt/libation-config:/config \
  -v /tmp/abs-test/libation-books:/data \
  -v /tmp/abs-test/audiobooks:/audiobooks \
  -v /tmp/abs-test/.env:/app/abs-mcp/.env:ro \
  -v /tmp/abs-test/libraries.yaml:/app/abs-mcp/libraries.yaml:ro \
  -p 8765:8765 \
  ghcr.io/stratus-ss/mcps/audiobook-ingestion-mcp:latest
```

Mount categories:

1. **Libation config** at `/config` -- `AccountsSettings.json`, `Settings.json`, `LibationContext.db`, `FileLocationsV2.json` (all 4 files from the production Libation directory)
2. **Libation books source** at `/data` -- this is `SOURCE_AUDIO_BOOK_DIRECTORY` inside the container
3. **ABS audiobooks destination** at `/audiobooks` -- this is `DESTINATION_BOOK_DIRECTORY`
4. **MCP `.env`** at `/app/abs-mcp/.env` (read-only) -- contains ABS URL, API token, library ID
5. **MCP `libraries.yaml`** at `/app/abs-mcp/libraries.yaml` (read-only) -- multi-library config

### Test book selection

Use a book that's marked `NotLiberated` in your Libation DB. The pipeline downloads it inside the container:

```python
result = list_library(status="NotLiberated", limit=1)
test_asin = result["books"][0]["asin"]
```

### Libation config gotcha: Settings.json Books path

Libation's `Settings.json` says `"Books": "/home/stratus/Libation/Books"` (or similar host path). Inside the container, that path is the Libation image's own internal location, not your `/data` mount. The downloaded book will land at `/home/stratus/Libation/Books/...` inside the container, NOT at `/data`.

**For end-to-end testing**, you have two options:

1. **Copy the downloaded book to /data** after the `download_books` step (what I did during T4 validation):
   ```bash
   docker exec mcp-test cp -r "/home/stratus/Libation/Books/<book>" /data/
   ```

2. **Edit Libation's Settings.json** before the test to point at `/data`:
   ```bash
   ssh root@<vm-ip> "python3 -c 'import json; \
     s = json.load(open(\"/opt/libation-config/Settings.json\")); \
     s[\"Books\"] = \"/data\"; \
     json.dump(s, open(\"/opt/libation-config/Settings.json\", \"w\"))'"
   ```
   Then restart the MCP container.

For production deployment: option 2 is the right answer -- set Libation's Books path to the mounted `/data` once and you're done.

## End-to-End Pipeline Test (MCP JSON-RPC)

The MCP server uses FastMCP streamable-http on path `/mcp` (NOT `/sse` -- that endpoint doesn't exist anymore). All calls go to `http://<vm-ip>:8765/mcp` with `Content-Type: application/json` and `Accept: application/json, text/event-stream`.

```bash
MCP_URL=http://<vm-ip>:8765/mcp

# 1. Initialize session, capture session ID
INIT=$(curl -s -N -i -X POST $MCP_URL \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"agent","version":"0"}}}' \
  --max-time 5)
SESSION=$(echo "$INIT" | grep -i "^mcp-session-id:" | tr -d '\r\n' | awk '{print $2}')

# 2. Send notifications/initialized (required by MCP spec, no response)
curl -s -o /dev/null -X POST $MCP_URL \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' --max-time 5

# 3. Call list_libraries to verify connectivity
curl -s -N -X POST $MCP_URL \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_libraries","arguments":{}}}' \
  --max-time 10
# Expected: "isError": false in the response data field
```

### The 6 pipeline steps to test (in order)

Each call is JSON-RPC `tools/call` against the session. Each should return `"isError": false`:

```bash
# 1. scan_audible
curl -s -N -X POST $MCP_URL -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"scan_audible","arguments":{}}}' \
  --max-time 60

# 2. download_books (use your test ASIN)
curl -s -N -X POST $MCP_URL -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SESSION" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"download_books\",\"arguments\":{\"asins\":[\"$TEST_ASIN\"]}}}" \
  --max-time 300

# 3. export_library
curl -s -N -X POST $MCP_URL -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"export_library","arguments":{}}}' \
  --max-time 60

# 4. organize_books (requires a book in /data -- see Libation config gotcha above)
curl -s -N -X POST $MCP_URL -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"organize_books","arguments":{"library":"adult","purchased_how_long_ago":0}}}' \
  --max-time 60

# 5. scan_audiobookshelf
curl -s -N -X POST $MCP_URL -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"scan_audiobookshelf","arguments":{"library":"adult","wait":25}}}' \
  --max-time 120

# 6. match_audiobookshelf
curl -s -N -X POST $MCP_URL -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"match_audiobookshelf","arguments":{"days_ago":1,"library":"adult"}}}' \
  --max-time 120
```

### Verify the organized file exists

```bash
ssh root@<vm-ip> "docker exec mcp-test find /audiobooks -type f"
# Expected: /audiobooks/Author/Series/Title/Book [ASIN].m4b
```

## Cleanup (ALWAYS run, even on failure)

```python
# 1. Stop and remove containers inside the VM
ssh root@<vm-ip> "docker rm -f abs-test mcp-test 2>/dev/null"

# 2. Stop and delete the VM
kvm_stop_vm(vm_name="<test-vm-name>", host="dl380")
kvm_delete_vm(vm_name="<test-vm-name>", remove_storage=True, host="dl380", confirm=True)
```

If `kvm_delete_vm` reports "running -- stop it first" (race condition), force-destroy via direct virsh:

```bash
ssh root@dl380 "virsh destroy <test-vm-name>"
# Then retry the kvm_delete_vm call
```

## Operational Notes (lessons from T4)

### DO NOT touch production VMs

The `archlinux-audiobookshelf` VM (id=9) is your PRODUCTION ABS instance at `kids-audio-books.x86experts.com`. Never call `guest_ping`/`guest_get_ip`/`guest_exec`/`virsh` against it for test purposes. If you need to verify the MCP works against a real ABS, use the test VM ABS, not production.

Other production VMs on dl380 to avoid: `arch-openclaw` (id=8), `centos-stream9-tailscale` (id=10), `kids-tv-arch` (id=11), `rh442-refresher` (id=12), `ubuntu22.04-sonarr` (id=13), `ubuntu24.04-Unifi_Controller` (id=14). Test VMs should have a clear `mcp-` or `abs-test-` prefix and be deleted after use.

### Base image disk location

The base qcow2 lives at `/var/lib/libvirt/images/base_images/archlinux-base-clone.qcow2` even though the libvirt VM is now named `archlinux`. The disk filename didn't get renamed when the VM was renamed. If you ever change the base image, both the disk filename AND the VM name should stay in sync (or the disk filename becomes a hidden source of truth).

### Known base image history (for archaeology)

- 2025-10-22: Original `archlinux-base-clone` created (per qcow2 timestamp)
- 2026-06-24: VM renamed from `archlinux-base-clone` to `archlinux` (id=22). At this point the qemu guest agent chardev in the running base was DISCONNECTED on the old image, which broke `kvm_clone_vm`'s post-clone agent checks. The fix (rebuild from scratch with working agent) is what unblocked Task 4 in `agent_planning/execution/mcp_containerization_docs/devlogs/`.

### `guest_exec` allowlist (do not waste time trying things outside it)

Allowed: `ls, pwd, whoami, date, uname, hostname, df, free, ps, ip, uptime, head, tail, wc, sort, uniq, which, type, cat, systemctl, journalctl, grep, ss, lsblk, lscpu, mount, id, stat, findmnt`

NOT allowed: `pacman`, `docker`, `curl`, `wget`, `ssh`, any shell metacharacters

**Workaround:** use SSH (after key injection) for anything outside the allowlist. SSH is the universal escape hatch.

### MCP server version discovery

Once the MCP container is running, you can discover the actual version:

```bash
curl -X POST http://<vm-ip>:8765/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"x","version":"0"}}}' \
  --max-time 5
# Look in the response for: "serverInfo":{"name":"Audiobook Ingestion","version":"1.28.0"}
```

Known version at the time of writing: **1.28.0** (June 2026).

## Failure Modes I Hit (and Fixes)

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| `virt-clone` error: `unrecognized arguments: --ram 4096 --vcpu 2` | virt-clone doesn't accept these | Drop the args (use defaults), or use Pattern B (direct qcow2 + virt-install) |
| `virt-clone` error: `Domain to clone must be shutoff` | Source VM is running | Use Pattern B: copy qcow2 + virt-install --import --noreboot |
| `kvm_start_vm` reports timeout, but `virsh list` shows running | Guest agent not yet started | Poll `kvm_guest_ping` repeatedly; up to 60s for first boot |
| `kvm_guest_ping` always returns "Guest agent is not connected" | Broken agent chardev in base image | Use Pattern B (don't rely on guest agent); SSH directly after key injection |
| `kvm_guest_get_ip` returns `fe80::...` (IPv6 link-local only) | Tool returns first address; IPv4 DHCP not yet acquired | Use `guest_exec("ip addr show")` to find the inet 192.168.X.X line |
| ABS `POST /api/init` returns 401 | Endpoint is `/init` (root), not `/api/init` | Use `POST /init` with body `{newRoot:{username,password}}` |
| ABS container crashes with `TypeError: Cannot read properties of undefined (reading 'username')` after init | Wrong body shape (used `newUser` or `root` instead of `newRoot`) | Use `{newRoot:{username,password}}` |
| MCP `scan_audible` returns `success: false` with "Cannot find settings files at /config-internal/Settings.json" | Libation CLI default path doesn't match your mount | Add `-e LIBATION_FILES_DIR=/config` to docker run |
| MCP `download_books` succeeds but file doesn't appear in `/data` | Libation's `Settings.json` "Books" path points at container-internal location | Either copy file to /data manually, or edit Settings.json + restart container |
| MCP `/sse` returns 404 | FastMCP moved to `/mcp` (streamable-http) | Use `/mcp` instead |
| `kvm_delete_vm` says "running -- stop it first" after `kvm_stop_vm` succeeded | Race condition between stop and delete | Direct `virsh destroy <vm>` then retry delete |

## Minimal Smoke Test (5 minutes)

If you just want to confirm the MCP container can talk to your test ABS, this is the minimum:

1. Create test VM via Pattern A or B
2. `docker run` ABS in the VM, `curl /healthcheck` (should return OK)
3. `curl POST /init` with admin creds, `curl POST /login` to get token, `curl POST /api/libraries` to create test library
4. SCP a 1-line `.env` (ABS URL + token + library ID) and a 1-line `libraries.yaml` (library name + ID) to the VM
5. `docker run` the MCP image with `LIBATION_FILES_DIR=/config` and the 5 mount categories
6. `curl POST /mcp` initialize, send `tools/call list_libraries` -- expect `isError: false`
7. Delete the VM

If step 6 returns the expected library, the MCP can talk to ABS. Pipeline end-to-end test (download, organize, match) is separate and takes longer.
