# Chatwoot / Behdashtik — Agent Guidelines

Read by Codex (`AGENTS.md`) and Claude Code (`CLAUDE.md`, a symlink to this file). One source of truth — edit here.

## Environment (this install)

This checkout is **not** a stock local dev setup — it is the live Behdashtik deployment. There is no Ruby, Bundler, rbenv, Overmind, or installed `node_modules` on the host; the repo is bind-mounted into containers at `/app`.

| Service | Container | Notes |
|---|---|---|
| Rails (Puma) | `chatwoot-rails-1` | `RAILS_ENV=production`, host port 4000 |
| Sidekiq | `chatwoot-sidekiq-1` | |
| Redis | `chatwoot-redis-1` | password protected, bound to `127.0.0.1` |
| Postgres | `chatwoot-postgres-1` | pgvector/pg16, bound to `127.0.0.1` |

Public URL: `support.behdashtik.ir` (nginx → `127.0.0.1:4000`, with a `/cable` upgrade block).

Run every Ruby/Node command inside the container:

```bash
docker exec chatwoot-rails-1 bundle exec rspec spec/path/to/file_spec.rb
docker exec chatwoot-rails-1 bundle exec rubocop -a app/models/foo.rb
docker exec chatwoot-rails-1 bundle exec rails runner '...'
docker exec chatwoot-rails-1 sh -c "cd /app && npx eslint app/javascript/path/to/File.vue"
```

**Applying changes:**
- `.erb` views, Ruby code → `docker restart chatwoot-rails-1` (no build needed).
- JS / Vue / SCSS → rebuild first, then restart:
  ```bash
  docker exec chatwoot-rails-1 sh -c "cd /app && RAILS_ENV=production NODE_ENV=production bin/vite build"
  docker restart chatwoot-rails-1
  ```
  Rails takes ~10–60s to come back; poll the URL instead of assuming.
- Assets land in `public/vite/assets/` (gitignored). Stale bundles from earlier builds stay behind — confirm which one is live by grepping the served page for the hashed filename.

**Committing:** the husky pre-commit hook runs `lint-staged`, which is not installed on the host and will fail. Lint the changed files in the container (above), then commit with `--no-verify`. Do not skip the lint itself.

## Build / Test / Lint

- **Setup** (fresh clone only): `bundle install && pnpm install`
- **Seed minimal test data**: `bundle exec rails db:seed`
- **Seed search/perf fixtures**: `bundle exec rails search:setup_test_data`
- **Seed richer account data**: `bundle exec rails runner "Internal::SeedAccountJob.perform_now(Account.find(<id>))"` (also exposed as Super Admin → Accounts → Seed)
- **Lint JS/Vue**: `pnpm eslint` / `pnpm eslint:fix`
- **Lint Ruby**: `bundle exec rubocop -a`
- **Test JS**: `pnpm test` or `pnpm test:watch`
- **Test Ruby**: `bundle exec rspec spec/path/to/file_spec.rb`, or `:LINE_NUMBER` for one example
- Always prefer `bundle exec` for Ruby CLI tasks. Run targeted tests only — never the full suite unless asked.

## Code Style

- **Ruby**: RuboCop rules, 150 character max line length. Use compact `module`/`class` definitions; avoid nested styles.
- **Vue/JS**: ESLint (Airbnb base + Vue 3 recommended)
- **Vue components**: PascalCase. **Events**: camelCase.
- **Vue API**: Composition API with `<script setup>` at the top
- **I18n**: no bare strings in templates
- **Error handling**: use custom exceptions (`lib/custom_exceptions/`)
- **Models**: validate presence/uniqueness, add proper indexes
- **Type safety**: PropTypes in Vue, strong params in Rails
- **Specs**: prefer `with_modified_env` over stubbing `ENV` directly. In parallel/reloading environments, compare `error.class.name` rather than constant class equality.

## Styling

- **Default to Tailwind utilities.** No custom CSS, no scoped CSS, no inline styles in new dashboard / `components-next` code.
- **Known exception**: the live-chat widget (`app/javascript/widget/`) predates that rule and still carries SCSS partials plus scoped `<style lang="scss">` blocks. Edit those in place — do not rewrite them into Tailwind as a side quest.
- **Colors**: see `tailwind.config.js`.
- RTL: alignment that follows the *text direction* uses logical utilities (`ms-`/`me-`, `text-start`). Alignment that follows a *fixed screen edge* stays physical (`ml-`/`pr-`, `text-right`) — mixing the two is how RTL layouts break.

## General Guidelines

- MVP focus: least code change, happy path only
- No unnecessary defensive programming; limit guards/fallbacks to what production has proven necessary
- Prefer minimal, readable code over elaborate abstractions
- Break work into small, testable units; iterate after confirmation
- Avoid writing specs unless explicitly asked
- Remove dead/unreachable/unused code
- Never write multiple versions or backups of the same logic — pick one and implement it

## Commits and PRs

- Conventional Commits: `type(scope): subject` — e.g. `feat(auth): add user authentication`
- Do not reference Claude, Codex, or any AI assistant in commit messages or PR bodies
- PR body: short user-facing paragraph → `Closes` section with issue links → `How to test` (features) or `How to reproduce` (bugfixes) → optional `What changed`
- Do not add a `How this was tested` section listing specs/commands

## Project-Specific

- **Translations**: only edit `en.yml` (backend) and `en.json` (frontend). Other languages come from the community.
- **Frontend**: use `components-next/` for message bubbles; the rest is being deprecated.
- **Branding**: for user-facing strings containing "Chatwoot" that should adapt to white-labeled installs, apply `replaceInstallationName` from `shared/composables/useBranding` in the UI layer rather than hardcoding brand copy.

## Enterprise Edition

Chatwoot has an Enterprise overlay under `enterprise/` that extends/overrides OSS code. Keep the two trees compatible. Reference: https://chatwoot.help/hc/handbook/articles/developing-enterprise-edition-features-38

Checklist for any change to core logic or public APIs:

- Search both trees before editing: `rg -n "FooService|ControllerName" app enterprise`
- New endpoints/services/models: decide whether Enterprise needs an override (`enterprise/app/...`) or an extension point (`prepend_mod_with`, hooks, config) instead of a hard fork
- Never hardcode instance- or plan-specific behavior in OSS — use configuration, feature flags, or extension points
- Keep request/response contracts identical across OSS and Enterprise; update both route sets together
- Mirror renames/moves of shared code into `enterprise/` to prevent drift
- Enterprise-only behavior in an existing OSS feature → add an Enterprise module via `prepend_mod_with`/`include_mod_with` rather than editing OSS files, especially for policies, controllers, and services. Enterprise-exclusive features live directly under `enterprise/`.
- Enterprise specs go in `spec/enterprise`, mirroring the OSS layout

## Knowledge Graph (graphify)

A persistent AST-derived graph lives in `graphify-out/` (god nodes, communities, cross-file relationships). Full setup notes: `docs/ai/AI_DEV_WORKFLOW.md`.

**Explore the graph before reading raw files:**

- `graphify query "<question>"` — scoped subgraph, far smaller than grep output
- `graphify path "<A>" "<B>"` — relationship between two concepts
- `graphify explain "<concept>"` — focused explanation
- `graphify-out/wiki/index.md` — broad navigation, when it exists
- `graphify-out/GRAPH_REPORT.md` — read only for broad architecture orientation
- Never read `graphify-out/graph.json` directly — it is ~14 MB of raw JSON. Use the CLI.
- Inspect only files relevant to the task; avoid full-directory reads.

**Keeping it current** (`scripts/ai-task`):

- `scripts/ai-task` — check status, build if missing (AST only, no LLM cost)
- `scripts/ai-task --update-graph` — rebuild after significant code changes or before committing a completed feature. Not after every small edit.
- `scripts/ai-task -- bundle exec rspec spec/...` — ensure the graph is fresh, then run a command
- If a rebuild produces fewer nodes than the stored graph, graphify refuses to overwrite it. When the shrink is expected (code was deleted), run `graphify update --force` — `scripts/ai-task` does not accept that flag.

## Final Reports

End every completed task with a compact report — no prose padding, one bullet per item. If nothing changed, say so in one line.

```
## Done
- Files changed: <list>
- Tests run: <command> → <result>
- Risks: <one line or "none">
```

## Custom Behdashtik Modules

These are Behdashtik overlays, **not** upstream Chatwoot. Do not mistake them for core functionality and do not remove or refactor them as dead code.

### Visitor Journey Tracking
Records dev-website page visits as private notes on the active conversation.
- Docs: `docs/behdashtik-visitor-journey-tracking.md`
- Files: `app/controllers/api/v1/behdashtik/`, `app/services/behdashtik/`, `public/js/behdashtik-journey-tracker.js`
- Needs the WordPress plugin (separate repo: `jolfaguy12-cell/wp-plugin`)
- **DEV only** (`dev.behdashtik.ir`). Do not enable in production without explicit approval.

### Widget Launcher and Font
- `public/fonts/iransans/` plus the `<style>` block in `app/views/widgets/show.html.erb` render the widget UI in IRANSansXFaNum. The block must stay **after** the Vite tags to win over Tailwind preflight.
- Launcher appearance (shape, brand color, icon, pulse, mobile offset) is overridden from the WordPress side, not here — see the `wp-plugin` repo. Keeping it there survives Chatwoot upgrades.
- RTL fixes in `widget/components/UnreadMessage*.vue` and `assets/scss/views/_conversation.scss` are upstream bug fixes, safe for LTR, but will be lost on a Chatwoot upgrade — re-apply them.

## Worktrees

Optional, for isolating parallel tasks. Nothing is committed for this yet — create it if you adopt the workflow.

- One git worktree + branch per task.
- Keep per-agent local setup out of the repo (e.g. an ignored `.codex/` or `.claude/` subdirectory) and use a dedicated Procfile for worktree process orchestration.
- Generate per-worktree DB name, Rails/Vite ports, and Redis DB index dynamically so parallel worktrees do not collide.
- Give each worktree its own Overmind socket and title.
