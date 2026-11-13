# Spike T-803: `llm-game-automation-mcp` review

Question: is `llm-game-automation-mcp` (task.md finding 2, line 74; decision 7, line 138) safe to build from reviewed source and try on the QA profile in T-804? And what would it add to BeamNG's built-in MCP?

Method: clone the source into a new empty folder (`H:\BelfastData\spikes\lga-mcp`), pin a commit, read it, and build it only if the read finds nothing unsafe. No prebuilt binaries, nothing run, no game.

## 1. Source availability (checked 2026-11-13, about 23:20 local)

| Check | Result |
|---|---|
| `git clone https://github.com/CorruptedAng3l/llm-game-automation-mcp` | `remote: Repository not found.` |
| `git ls-remote` on the same URL | `Repository not found` |
| `api.github.com/repos/CorruptedAng3l/llm-game-automation-mcp` | HTTP 404 (a renamed repo would answer 301) |
| `api.github.com/users/CorruptedAng3l` | 404 `Not Found`: the owner account itself is gone (deleted, renamed or suspended) |
| `github.com/CorruptedAng3l/llm-game-automation-mcp` (web) | 404 |
| GitHub repo search `llm-game-automation in:name` | `total_count: 0`: no fork or re-upload under that name |
| GitHub user search `CorruptedAng` | Only `CorruptedAngel7106` and `corruptedangel`: different accounts, not checked further |
| Software Heritage origin lookup | `Origin ... not found!` |
| Wayback Machine (repo and profile) | `archived_snapshots: {}` |
| Web search for the exact name | No hit for the project |
| Local copies (repo grep, `H:\BelfastData`) | None. Only the task.md lines 74, 138, 340, 388 mention it |

So there is no commit to pin and no code to read. The spike folder was created, stayed empty after the failed clone, and was removed.

## 2. Review items from the brief

| Item | Finding |
|---|---|
| What it injects and how | Not verifiable. task.md line 74 says it injects a DLL into the game process. No source exists to check that. |
| Input and memory access | Not verifiable. DLL injection runs code inside the game process with full access to its memory. |
| Network calls, telemetry | Not verifiable. |
| MCP server exposure (bind address, auth) | Not verifiable. |
| Build scripts and dependencies | Not verifiable. Nothing was built (see section 3). |
| Licence | Unknown. With no repo, there is no licence to rely on. |
| EULA | BeamNG EULA section 4.2 forbids reverse engineering, disassembly and decompiling of the Software, and circumventing copy protection. It allows modding of content files only within the modding guidelines. Section 3.3 grants no right to modify the Software's code. Section 10.4 limits BeamNG's liability once the Software "has been modified in any manner". The EULA never mentions injection, cheats or bots. A DLL injected into `BeamNG.drive.x64.exe` modifies the running Software outside the modding route: it is not a content-file mod and is not covered by the modding guidelines. At best it is a grey area, and BeamNG may end the licence for a breach of 4.2. |
| Provenance | The repo was six days old when task.md was written (line 74). Its owner account has since gone. The prebuilt binaries are unsigned. For a tool that injects code, that is a red flag in its own right. |

## 3. Build

Not built. There is no source. No prebuilt binary or release asset was downloaded or run, and no process was touched.

## 4. What it would add beyond the built-in MCP

From docs/spikes/mcp.md (102 tools in 0.40.5, loopback only, no injection):

- Vehicle control already exists: `inject_input` (steering, throttle and brake events into the vehicle Lua VM), `run_lua_vehicle`, AI `drive_to` / `set_ai`, `get_electrics` (gear, rpm, inputs), `get_status`, `screenshot_image`, `get_vehicle_damage`, `recover_vehicle`, `set_position`.
- What remains is "real gamepad driving" (T-804): input that enters through the OS or device layer, as a VR driver's pad or wheel does, rather than through Lua. If we need that, an OS-level virtual controller or `inject_input` with the same input filter as a pad does it without putting code inside the game process. T-804 would need to show this gap really exists before any injection tool is reconsidered.
- The tool's own published road results were modest (GTA V car: 6 of 11 arrivals, line 74). Nothing in the plan depends on it (line 74, right column).

## 5. Verdict

**No-go for T-804.** Guard rail 11 needs the code read and pinned to a commit. Both are impossible: the repository and its owner account have gone from GitHub, and no archive or fork exists. The prebuilt binaries are unsigned and are ruled out. On top of that, the tool works by DLL injection, which the EULA does not clearly allow, and it adds little to the built-in MCP.

What could reopen this (a new task, not T-804 as written):

1. The source reappears at a public URL that can be checked against the original (same author or a traceable fork), with a licence.
2. A new review at a pinned commit covers the items in section 2 and ends in go.
3. A concrete need is shown that the built-in MCP and a non-injecting virtual controller cannot meet.
4. The user accepts the EULA risk in writing (task.md section 4).

Not verified: anything about the code itself. This review covers availability, the EULA text and overlap with the built-in MCP only.
