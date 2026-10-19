# Main integration review — 2026-10-19

## Scope and source revisions

The user explicitly requested review and integration of both development branches into `main`. Validation takes place in a separate worktree; existing development worktrees and application data are not reset or overwritten.

| Ref | Reviewed revision | Scope |
| --- | --- | --- |
| `origin/main` | `f3ed3f69c86d51693af348f86ddbf8a6311598ed` | Starting main revision |
| `codex/ordinary-chat-conflicts` | `b7fe2ffa8d9378041496cee80691e64b26309af4` | Embedding/context reliability, unified tools, MCP/CLI and Markdown |
| `codex/tool-mcp-cli` | `51148132c4379af6266cf27ef48d9e3842aa64c3` | Project run control, approvals, workspace changes, conversation UI and Markdown |

## Conflict review

The ordinary-chat branch incorporates the same tool implementation as `2b22694`, followed by Markdown and delivery documentation changes. The project-task branch descends from `2b22694` and adds the later execution and UI features. Git cannot infer that relationship from the separate ordinary-chat commit history.

The first merge is clean. The second merge has 21 conflicting files: 16 backend/configuration files, `README.md`, two development documents, the frontend lockfile and `RunDetails.tsx`.

Resolutions retain the newer project-task implementation, including authorization hooks, cancellation, project context, run recovery and database fixtures. Both branches have identical Markdown component, stylesheet, component tests and timeline integration. The frontend lockfile retains the project-task test dependencies and installs with `npm ci` without regeneration. Historical ordinary-chat delivery notes are preserved alongside current project-task progress.

After resolving conflicts, the application source, configuration example and dependency locks exactly match `5114813`. The only subsequent code-tree change is the test synchronization fix described below; no production behavior is changed during integration.

## Test reliability fix

An initial run without a test database reported `197 passed, 48 skipped, 1 failed`. It is not counted as the integration gate. The failing `test_process_tree_reaped_including_after_parent_exits[parent_exit]` used a fixed 0.3-second parent lifetime. Under startup load, the child could be reaped before writing its PID, so the test did not establish its own prerequisite.

The parent now waits for an explicit release file. The test first observes the child PID and confirms it is alive, then releases the parent and still checks that the child is reaped and the command did not time out. Timeout and cancellation cases retain their original assertions. This removes the startup race without weakening process cleanup coverage.

## Validation

Install locked dependencies with `uv sync --locked` in `backend` and `npm ci` in `frontend`, then run `./scripts/verify-baseline.ps1` from the repository root.

| Check | Result |
| --- | --- |
| Backend regression with real PostgreSQL/pgvector | 246 passed, zero skipped, zero failed |
| Frontend regression | 27 passed across 5 files |
| Process cleanup tests after synchronization fix | 9 passed |
| Ruff and offline lock consistency | Passed |
| Alembic upgrade, downgrade to `0007_message_run_link`, re-upgrade to `0010_workspace_changes` | Passed |
| Offline evaluation | Matches the recorded 36-sample baseline, including 4 existing quality failures |
| TypeScript and Vite production build | Passed |
| npm audit | Zero reported vulnerabilities |
| Conflict markers, staged whitespace and document links | Checked before commit |

Docker Desktop was initially unavailable. The gate was rerun after the engine started; the failed startup attempt is not counted as a successful database check. Each baseline run uses its own loopback-only temporary database and cleans up its own container and network. No development database migrations are applied by this integration.

## Delivery and limits

Both development branch tips remain ancestors of the main integration result through merge commits; neither branch history is rewritten. Push uses a normal fast-forward update to `origin/main`, after a fresh remote-state check. The source branches remain available.

The review supports merging the current development baseline. It does not claim a public-production release or complete manual acceptance. Stage 4 still needs the documented real-model command failure/repair, native folder selection and reconnect demonstrations; stages 5–7 remain pending. Local execution remains disabled by default, execution approvals remain in place, and the application does not provide an operating-system sandbox. The four recorded memory-quality failures remain documented.
