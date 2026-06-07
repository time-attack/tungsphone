# AGENTS.md

This file governs how Codex performs Git operations, generates commit messages, and runs the release process in this repository.

This project is an open-source project maintained on GitHub. Day-to-day development mainly includes:

- Improving existing features
- Fixing bugs
- Adding new features
- Updating documentation
- Publishing new versions

When performing Git-related operations, Codex must strictly follow the rules in this file.

---

## 1. AGENTS.md Usage Rules

### 1. File naming rule

If you want Codex to read the project rules automatically, the file name should be:

```text
AGENTS.md
```

Do not rename this file to:

```text
GIT.md
SKILL.md
AGENT.md
agents.md
```

If an `AGENTS.md` already exists in the project, do not create a second file with the same name and do not overwrite the original. Instead, merge the Git rules, commit process, and release process from this file into the existing `AGENTS.md`.

### 2. Handling multiple AGENTS.md files

If the repository already has other `AGENTS.md` files, handle them in this priority order:

1. Root `AGENTS.md` already exists: merge this file's content into it, ideally under a `Git Workflow Rules` section.
2. Subdirectory `AGENTS.md` exists: subdirectory rules only apply to tasks in that subdirectory; the root should still keep the global Git rules.
3. If existing `AGENTS.md` rules conflict with this file, the more specific rule wins; if both are global rules, the user's most recent explicit request wins.
4. Do not delete existing project rules, build rules, test rules, or code-style rules just because you are adding Git rules.

### 3. Recommended organization

The recommended structure for a root `AGENTS.md`:

```text
# AGENTS.md

## Project Overview
## Development Rules
## Testing Rules
## Git Workflow Rules
## Release Process Rules
```

This file primarily provides:

```text
Git Workflow Rules
Release Process Rules
```

---

## 2. Core Principles

### 1. All critical operations require user confirmation

The following must be shown to the user first, and you must wait for explicit confirmation before proceeding:

- commit message
- `git add`
- `git commit`
- `git push`
- release branch creation or switching
- tag creation
- GitHub Release title
- GitHub Release content
- GitHub Release creation
- merging a release branch into main

Until the user explicitly confirms, you may not directly commit, push, create tags, or create a GitHub Release.

### 2. Forbidden dangerous operations

Unless the user explicitly requests it, do not run the following commands:

```bash
git reset --hard
git push --force
git clean -fd
git branch -D
git push origin --delete
```

Note especially: do not delete the release branch after a release is complete.

---

## 3. Branch Naming Rules

### 1. Branch name format

Branch names use a single format:

```text
type-short-description
```

Separate type and description with `-`, not `/`.

### 2. Common branch types

```text
feature-xxx     New feature development
fix-xxx         Normal bug fix
hotfix-xxx      Urgent bug fix
release-x.x.x   Release branch
docs-xxx        Documentation changes
refactor-xxx    Code refactoring
chore-xxx       Build, config, dependency adjustments
perf-xxx        Performance optimization
```

### 3. Branch name examples

Recommended:

```text
feature-ui-query
feature-screenshot-api
fix-click-position
fix-rootless-install
hotfix-launch-crash
release-1.1.0
docs-update-readme
refactor-element-parser
chore-update-makefile
perf-optimize-screenshot
```

Not recommended:

```text
feature/ui-query
release/v1.1.0
release-v1.1.0
fix_click_position
Feature-Login
my-branch
update
```

### 4. main branch rules

The `main` branch represents the latest stable version.

After a release is complete, the release branch must be merged back into `main`, so that `main` always corresponds to the latest official release.

---

## 4. Commit Message Rules

### 1. Commit message format

Commit messages use a single format:

```text
type: description
```

Write the description in English, concise and clear.

### 2. Common commit types

```text
feat      New feature
fix       Bug fix
docs      Documentation changes
refactor  Code refactoring
style     Formatting changes that do not affect logic
test      Test-related
chore     Build, config, dependency, script, and other chores
perf      Performance optimization
revert    Revert a commit
release   Version release
```

### 3. Commit message examples

```text
feat: add UI element query endpoint
feat: add screenshot endpoint
fix: fix tap coordinate offset issue
fix: fix install failure in rootless environment
docs: update README installation instructions
refactor: refactor AXRuntime element parsing logic
chore: adjust Makefile packaging config
perf: improve screenshot response speed
release: publish v1.1.0
```

### 4. Discouraged commit messages

```text
update
fix bug
commit code
changed some stuff
optimize
fix
```

---

## 5. Commit-to-Repository Workflow

### 1. Triggers

Enter the "commit to repository" workflow when the user inputs any of:

```text
commit code
commit to repository
commit and push
git commit code to repository
generate commit message
```

### 2. Steps

#### Step 1: Check current status

You must first run:

```bash
git status
git diff --stat
git diff
```

If there are untracked files, mention them in the summary as well.

#### Step 2: Summarize the current changes

Summarize this change set based on the current uncommitted code.

Output format:

```text
Summary of changes:

1. xxx
2. xxx
3. xxx
```

#### Step 3: Generate a commit message

Automatically generate one compliant commit message based on the code changes.

For example:

```text
fix: fix tap coordinate offset issue
```

If the change set spans multiple directions, determine the primary purpose first.

Decision rules:

```text
New feature        -> feat
Bug fix            -> fix
Documentation      -> docs
Refactoring        -> refactor
Build/config       -> chore
Performance        -> perf
Version release     -> release
```

#### Step 4: Require user confirmation before committing

Before running any commit command, you must display:

```text
Summary of changes:

1. xxx
2. xxx
3. xxx

Suggested commit message:

fix: fix tap coordinate offset issue

About to run:

git add .
git commit -m "fix: fix tap coordinate offset issue"
git push

Confirm commit and push?
```

Only proceed after the user explicitly confirms.

#### Step 5: Commit and push after confirmation

After the user confirms, run:

```bash
git add .
git commit -m "confirmed commit message"
git push
```

If the current branch has no associated remote branch, run:

```bash
git push -u origin current-branch-name
```

---

## 6. Version Release Workflow

### 1. Triggers

Enter the "version release" workflow when the user inputs:

```text
publish 1.1.0
publish v1.1.0
release version 1.1.0
release 1.1.0
create release 1.1.0
```

### 2. Version recognition rules

If the user inputs:

```text
publish 1.1.0
```

interpret it as:

```text
Version: 1.1.0
release branch: release-1.1.0
tag: v1.1.0
GitHub Release title: ProjectName v1.1.0
```

If the project name can be clearly identified from the repository name or README, use it to build the Release title.

For example:

```text
iOS MCP v1.1.0
```

If the project name is unclear, ask the user to confirm the title before creating the Release.

### 3. release branch rules

Release branches use a single format:

```text
release-version
```

Examples:

```text
release-1.0.0
release-1.0.1
release-1.1.0
```

Do not use:

```text
release/v1.1.0
release-v1.1.0
release_1.1.0
```

### 4. tag rules

Tags use a single format:

```text
vversion
```

Examples:

```text
v1.0.0
v1.0.1
v1.1.0
```

### 5. GitHub Release title rules

The GitHub Release title uses:

```text
ProjectName vversion
```

For example:

```text
iOS MCP v1.1.0
```

---

## 7. Release Execution Process

### Step 1: Check repository status

Before releasing, you must run:

```bash
git status
git branch --show-current
git fetch --all --tags
```

If there is uncommitted code, you must stop the release process and ask the user to commit or handle the current changes first.

If `git fetch --all --tags` fails due to network, DNS, GitHub connectivity, authentication, or sandbox permissions, handle it as follows:

1. You may retry the same command once.
2. If the failure is caused by the sandbox network or `.git` write permission, you may retry with elevated permissions.
3. If it still fails after retrying, you must stop the release process.
4. When stopping, clearly explain the current local state, the failed command, the failure reason, and the next command to run when resuming the release.
5. Until the remote sync succeeds, you may not continue to create tags, push tags, or create a GitHub Release.

### Step 2: Confirm target version info

Generate the version info from the user input and display it:

```text
About to release version: 1.1.0

release branch:
release-1.1.0

tag:
v1.1.0

GitHub Release title:
iOS MCP v1.1.0
```

### Step 3: Check whether the tag already exists

Run:

```bash
git tag --list "v1.1.0"
```

If the tag already exists, you must stop the release process and tell the user this version already exists and cannot be released again.

### Step 4: Create or switch to the release branch

First switch to main and pull the latest code:

```bash
git switch main
git pull
```

Check whether the release branch exists:

```bash
git branch --list "release-1.1.0"
git branch -r | grep "origin/release-1.1.0"
```

If it exists neither locally nor remotely, create the release branch from main:

```bash
git switch -c release-1.1.0
```

If it already exists remotely, switch to the existing release branch:

```bash
git switch release-1.1.0
git pull
```

### Step 5: Check and update the version number

Based on the project, check files that may contain a version number, for example:

```text
README.md
control
package.json
Info.plist
Makefile
other project config files
```

If a version number needs updating, you must show the change plan first and wait for user confirmation before editing.

If no version-number file needs changing, do not force any change.

### Step 6: Build release packages and check Release assets

Before creating a GitHub Release, you must build and check all three release packages:

```bash
printf "1\n1\n" | ./build.sh
printf "2\n1\n" | ./build.sh
printf "3\n1\n" | ./build.sh
```

After building, you must verify the following files exist:

```text
rootful:  packages/com.witchan.ios-mcp_version_iphoneos-arm.deb
rootless: packages/com.witchan.ios-mcp_version_iphoneos-arm64.deb
roothide: packages/com.witchan.ios-mcp_version_iphoneos-arm64e.deb
```

If any file is missing, you must stop the release process and ask the user to build the release packages first. Do not upload old packages, debug packages, packages with the wrong version number, or only some of the assets.

The Release assets section must be written into the GitHub Release in this format:

```markdown
## Release assets

rootful: com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
rootless: com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
roothide: com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb
```

### Step 7: Generate the Release content

Generate the version notes from the commits between the previous tag and the current version.

First find the most recent tag:

```bash
git tag --sort=-v:refname
```

View the commit log:

```bash
git log previous-tag..HEAD --oneline
```

Generate the Release content in English:

```markdown
## What's New

- xxx
- xxx

## Fixes

- xxx
- xxx

## Release assets

rootful: com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
rootless: com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
roothide: com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb
```

Do not add a `Notes` section by default. Only add a notes section when the user explicitly requests it.

If this version is mainly bug fixes, keep the same format but focus on:

```markdown
## Fixes
```

If this version is mainly improvements, keep the same format but focus on:

```markdown
## What's New
```

### Step 8: Release content requires user confirmation

Before creating the GitHub Release, you must display the complete Release content.

Output format:

```text
About to release version: 1.1.0

release branch:
release-1.1.0

tag:
v1.1.0

GitHub Release title:
iOS MCP v1.1.0

Release content:

## What's New

- xxx
- xxx

## Fixes

- xxx

## Release assets

rootful: com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
rootless: com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
roothide: com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb

Release assets:
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb

Operations about to run:

1. Create or switch to the release-1.1.0 branch
2. Check and update the version number
3. Commit release changes
4. Push the release branch
5. Merge release-1.1.0 into main
6. Create tag v1.1.0 on main
7. Push main
8. Push the tag
9. Build and check all three release packages
10. Create/update the GitHub Release via the GitHub REST API
11. Upload the three Release assets

Confirm the release?
```

Only proceed after the user explicitly confirms.

---

## 8. Release Operations After User Confirmation

### 1. Commit version changes on the release branch

If version-number files or release-related files were modified, run:

```bash
git add .
git commit -m "release: publish v1.1.0"
```

If no files were modified, do not force a commit.

### 2. Push the release branch

```bash
git push -u origin release-1.1.0
```

If the push fails, handle it as follows:

1. You may retry the same command once.
2. If it is a network or sandbox issue, you may retry with elevated permissions.
3. If it still fails, you must stop the rest of the process — you may not continue merging main, creating a tag, or creating a GitHub Release.
4. When stopping, explain the current branch, current commit, whether it was committed locally, whether the remote was updated, and the next command to run when resuming.

### 3. Merge the release branch into main

When releasing, after the release branch is confirmed complete, it must be merged back into main.

Display again before running:

```text
About to merge release-1.1.0 into main:

git switch main
git pull
git merge --no-ff release-1.1.0 -m "release: merge v1.1.0 into main"
git push origin main

Confirm merge into main?
```

After the user confirms, run:

```bash
git switch main
git pull
git merge --no-ff release-1.1.0 -m "release: merge v1.1.0 into main"
git push origin main
```

If `git push origin main` fails, you must stop the tag and GitHub Release steps. Only after main is successfully pushed to the remote may you create the tag.

### 4. Create the tag on main

After merging into main, create the tag on the main branch:

```bash
git tag -a v1.1.0 -m "release: publish v1.1.0"
```

Push the tag:

```bash
git push origin v1.1.0
```

If pushing the tag fails, you must stop the GitHub Release creation. Only after the tag is successfully pushed to the remote may you create the GitHub Release.

### 5. Build release packages and check assets

```bash
printf "1\n1\n" | ./build.sh
printf "2\n1\n" | ./build.sh
printf "3\n1\n" | ./build.sh
ls -l packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
ls -l packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
ls -l packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb
```

### 6. Create the GitHub Release

For this project, prefer the GitHub REST API to create a GitHub Release; do not rely on the `gh` command by default.

GitHub REST API rules:

1. Use `git credential fill` to read the GitHub token from local Git credentials.
2. Never print the token in logs, terminal output, or the final reply.
3. If the GitHub token cannot be read, stop and ask the user to configure GitHub credentials.
4. First check whether the Release exists via `GET /repos/witchan/ios-mcp/releases/tags/v1.1.0`.
5. If the Release does not exist, create it via `POST /repos/witchan/ios-mcp/releases`.
6. If the Release already exists, update its title and content via `PATCH /repos/witchan/ios-mcp/releases/{release_id}`.
7. Before uploading assets, check whether an asset with the same name already exists.
8. If an asset with the same name exists, only after the user has confirmed "overwrite/update assets" may you delete the old asset and upload the new one.
9. Upload the following three assets:

```text
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb
```

After creating/updating, you must output the GitHub Release URL and the download links for all three assets.

Do not create a GitHub Release without the user confirming the Release content.

---

## 9. Release Branch Retention Rules

After a release is complete, you must keep the release branch.

For example, after releasing `1.1.0`, keep:

```text
release-1.1.0
v1.1.0
GitHub Release: iOS MCP v1.1.0
```

Do not run automatically:

```bash
git branch -d release-1.1.0
git branch -D release-1.1.0
git push origin --delete release-1.1.0
```

Unless the user explicitly says "delete the release branch", you may not delete any release branch.

---

## 10. Output Format After Release

After a release is complete, output:

```text
Release complete:

Version: 1.1.0
release branch: release-1.1.0
tag: v1.1.0
GitHub Release: iOS MCP v1.1.0
main branch: synced to v1.1.0
release branch: retained
Release assets:
rootful: com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb
rootless: com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb
roothide: com.witchan.ios-mcp_1.1.0_iphoneos-arm64e.deb
```

---

## 11. Common Scenario Rules

This section is for deciding the commit message type and categorizing Release content. The official GitHub Release must still follow the Step 7 format and include `Release assets`.

### 1. Releasing after an improvement

If the user says:

```text
improve the screenshot feature, publish 1.1.0
```

Prefer this commit message:

```text
perf: improve screenshot feature
```

If it is only a UX improvement with no performance impact, you may also use:

```text
feat: improve screenshot feature
```

Prefer placing the Release content under:

```markdown
## Improvements

- improve screenshot feature
```

### 2. Releasing after a bug fix

If the user says:

```text
fix the tap coordinate offset issue, publish 1.1.1
```

Prefer this commit message:

```text
fix: fix tap coordinate offset issue
```

Prefer placing the Release content under:

```markdown
## Fixes

- fix tap coordinate offset issue
```

### 3. Releasing after a new feature

If the user says:

```text
add a screenshot endpoint, publish 1.2.0
```

Prefer this commit message:

```text
feat: add screenshot endpoint
```

Prefer placing the Release content under:

```markdown
## What's New

- add screenshot endpoint
```

### 4. Documentation changes

If you are only updating the README, install instructions, or usage instructions, use this commit message:

```text
docs: update README usage instructions
```

---

## 12. BigBoss Submission / Update Process

### 1. Triggers

Enter the "BigBoss submission / update" process when the user inputs any of:

```text
publish to bigboss
publish to BigBoss
submit to bigboss
submit to BigBoss
update bigboss
update BigBoss
```

### 2. Base rules

BigBoss is a third-party repository; submissions enter the BigBoss review process. This is an externally visible submission, so you must confirm with the user first.

This project's BigBoss update form script is fixed as:

```text
Script: scripts/submit_bigboss_update.py
Default Package Name: iOS MCP
Your Name: witchan
Email: witchan028@126.com
```

When running the script, pass only:

```text
Version
Changes Made
Package Name (optional; roothide must use iOS MCP (roothide))
deb path
```

When submitting rootful and rootless to BigBoss, the BigBoss form `Package Name` uses the default:

```text
iOS MCP
```

When submitting roothide to BigBoss, you must use a separate package identity to avoid mixing it with the normal rootless package:

```text
control Package: com.witchan.ios-mcp-roothide
control Name: iOS MCP (roothide)
BigBoss Package Name: iOS MCP (roothide)
```

The roothide BigBoss package is a repackaged artifact dedicated to BigBoss submission; it does not replace the normal roothide asset in the GitHub Release.

The BigBoss `Changes Made` should be short. Do not write long Release notes. Recommended format:

```text
EN: xxx.
```

If the user provides incomplete change notes, complete them and show them to the user for confirmation before submitting.

### 3. User confirmation content

After triggering the BigBoss process, you must first have the user confirm:

About to submit to BigBoss:

Version:
1.1.0

Changes Made:
EN: xxx.

rootful deb:
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb

rootless deb:
packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb

roothide BigBoss deb:
packages/com.witchan.ios-mcp-roothide_1.1.0_iphoneos-arm64e.deb

About to run:

Temporarily modify control:

```text
Package: com.witchan.ios-mcp-roothide
Name: iOS MCP (roothide)
```

Build the roothide BigBoss package:

```bash
printf "3\n1\n" | ./build.sh
```

Restore control:

```text
Package: com.witchan.ios-mcp
Name: iOS MCP
```

Check that control is restored:

```bash
git diff -- control
```

Submit the BigBoss form:

```bash
python3 scripts/submit_bigboss_update.py \
  --version 1.1.0 \
  --changes "EN: xxx." \
  --response-out .codex-session-data/bigboss_update_1.1.0_rootful_response.html \
  packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm.deb \
  --submit

python3 scripts/submit_bigboss_update.py \
  --version 1.1.0 \
  --changes "EN: xxx." \
  --response-out .codex-session-data/bigboss_update_1.1.0_rootless_response.html \
  packages/com.witchan.ios-mcp_1.1.0_iphoneos-arm64.deb \
  --submit

python3 scripts/submit_bigboss_update.py \
  --package-name "iOS MCP (roothide)" \
  --version 1.1.0 \
  --changes "EN: xxx." \
  --response-out .codex-session-data/bigboss_update_1.1.0_roothide_response.html \
  packages/com.witchan.ios-mcp-roothide_1.1.0_iphoneos-arm64e.deb \
  --submit
```

Confirm submission to BigBoss review?

Only execute the submission after the user explicitly confirms.

### 4. deb file rules

By default, submit the three packages supported by BigBoss:

```text
rootful:  packages/com.witchan.ios-mcp_version_iphoneos-arm.deb
rootless: packages/com.witchan.ios-mcp_version_iphoneos-arm64.deb
roothide: packages/com.witchan.ios-mcp-roothide_version_iphoneos-arm64e.deb
```

roothide does not submit the normal Release package directly:

```text
packages/com.witchan.ios-mcp_version_iphoneos-arm64e.deb
```

Before submitting roothide to BigBoss, you must temporarily modify `control`:

```text
Package: com.witchan.ios-mcp-roothide
Name: iOS MCP (roothide)
```

Then build the official roothide package:

```bash
printf "3\n1\n" | ./build.sh
```

After building, you must verify the output is:

```text
packages/com.witchan.ios-mcp-roothide_version_iphoneos-arm64e.deb
```

After building the roothide BigBoss package, you must restore `control` to the normal package config:

```text
Package: com.witchan.ios-mcp
Name: iOS MCP
```

After restoring, you must check `git diff -- control` to confirm no temporary package-name change is left behind. Temporarily modifying `control` is only for building the BigBoss roothide package; do not commit these temporary changes by default.

### 5. Pre-execution checks

After the user confirms, you must first generate or confirm the roothide BigBoss package before executing:

1. If `packages/com.witchan.ios-mcp-roothide_version_iphoneos-arm64e.deb` does not exist, temporarily modify `control` per the previous section, run `printf "3\n1\n" | ./build.sh` to build, then restore `control`.
2. If the roothide BigBoss package already exists, you must still check the filename to confirm the package name is `com.witchan.ios-mcp-roothide`; do not upload the normal `com.witchan.ios-mcp_version_iphoneos-arm64e.deb`.
3. After building or confirming, run the following checks:

```bash
git status --short --branch
ls -l packages/com.witchan.ios-mcp_version_iphoneos-arm.deb
ls -l packages/com.witchan.ios-mcp_version_iphoneos-arm64.deb
ls -l packages/com.witchan.ios-mcp-roothide_version_iphoneos-arm64e.deb
git diff -- control
python3 scripts/submit_bigboss_update.py --help
```

If the rootful or rootless deb does not exist, you must stop and ask the user to build the release packages first.
If the roothide BigBoss deb still does not exist after building, you must stop — you may not substitute the normal roothide Release package.
You may not submit old versions or packages from the wrong path.
If `git diff -- control` still shows the roothide temporary package-name change, you must restore `control` before submitting to BigBoss.

### 6. Execute the submission

After confirming everything is correct, submit in order:

```bash
python3 scripts/submit_bigboss_update.py \
  --version version \
  --changes "confirmed Changes Made" \
  --response-out .codex-session-data/bigboss_update_version_rootful_response.html \
  packages/com.witchan.ios-mcp_version_iphoneos-arm.deb \
  --submit

python3 scripts/submit_bigboss_update.py \
  --version version \
  --changes "confirmed Changes Made" \
  --response-out .codex-session-data/bigboss_update_version_rootless_response.html \
  packages/com.witchan.ios-mcp_version_iphoneos-arm64.deb \
  --submit

python3 scripts/submit_bigboss_update.py \
  --package-name "iOS MCP (roothide)" \
  --version version \
  --changes "confirmed Changes Made" \
  --response-out .codex-session-data/bigboss_update_version_roothide_response.html \
  packages/com.witchan.ios-mcp-roothide_version_iphoneos-arm64e.deb \
  --submit
```

If an earlier submission succeeded but a later one failed, you must clearly tell the user which packages BigBoss may have already received and that the state is partially submitted. Do not automatically re-submit a package that already succeeded unless the user explicitly confirms a retry.

### 7. Output format after completion

After submission completes, output:

```text
BigBoss submission complete:

Version: 1.1.0
rootful: submitted
rootless: submitted
roothide: submitted
Review status: awaiting BigBoss review
Response records:
.codex-session-data/bigboss_update_1.1.0_rootful_response.html
.codex-session-data/bigboss_update_1.1.0_rootless_response.html
.codex-session-data/bigboss_update_1.1.0_roothide_response.html
```

---

## 13. Final Requirements

Codex must always follow these requirements:

1. To have Codex read the rules automatically, use the file name `AGENTS.md`.
2. If the project already has `AGENTS.md`, merge this file's content into it; do not overwrite existing rules.
3. Use `-` to separate branch names, not `/`.
4. Release branches use `release-version`.
5. Tags use `vversion`.
6. Commit messages must be confirmed by the user first.
7. Release content must be confirmed by the user first.
8. After a release, the release branch must be merged into main.
9. After a release, the release branch must be retained.
10. Without user confirmation, you may not commit, push, create tags, or create a GitHub Release.
11. Without user confirmation, you may not submit a BigBoss update form.
12. GitHub Releases are created/updated via the GitHub REST API by default, uploading all three release packages: rootful, rootless, roothide.
13. GitHub Release content uses English by default and includes Release assets; no notes section is added by default.
14. When GitHub network or sandbox operations fail, you must stop the dangerous follow-up steps and clearly explain the local state and recovery commands.
15. BigBoss submits the three packages — rootful, rootless, roothide — by default; roothide must be repackaged with `com.witchan.ios-mcp-roothide` and `iOS MCP (roothide)` before submission.
