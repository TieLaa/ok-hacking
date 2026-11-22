---
name: deploy
description: Commit completed repository changes, create the next annotated version tag, and push the commit and tag to the publishing remote. Use when the user asks to deploy, release, publish a version, create or push a release tag, run `deploy` for a stable release, `deploy beta` for a beta prerelease, or `deploy alpha`/`release alpha` for an alpha prerelease.
---

# Deploy

Use this workflow to turn validated local changes into one commit and one annotated version tag, then push both to the publishing remote. If the user explicitly requests a local-only deployment, stop after creating the local tag.

## Variants

- `deploy` or `deploy release`: create a stable tag, such as `v3.3.54`.
- `deploy beta`: create a beta tag, such as `v3.3.55-beta.1`.
- `deploy alpha` or `release alpha`: create an alpha tag, such as `v3.3.55-alpha.1`.

Stable versions increment the patch number of the latest stable `vMAJOR.MINOR.PATCH` tag. For alpha and beta independently:

- Continue an unreleased prerelease line by incrementing its suffix, for example `v3.3.55-beta.1` to `v3.3.55-beta.2`.
- If no prerelease exists for a version beyond the latest stable release, begin the next patch at `.1`, for example stable `v3.3.54` creates `v3.3.55-beta.1`.
- Treat a prerelease whose base version has already been released as closed; start the following patch rather than tagging a prerelease after its stable release.

Use `scripts/next_tag.py` to calculate tags; it ignores tags outside these formats.

## Workflow

1. Inspect the worktree with `git status --short --branch` and inspect the relevant diff.
   Do not include unrelated user changes in the deployment commit. If the intended commit contents are ambiguous, confirm them before staging.
2. Confirm the changes are ready to ship.
   Run appropriate focused tests or the repository's documented verification. If verification fails, stop before committing or tagging unless the user explicitly accepts the failure.
3. Read the most recent non-merge commit subject before composing the new message:

   ```powershell
   git log --no-merges -1 --format=%s
   ```

   Write a concise commit message describing the staged changes in the same natural language as that subject. For example, if the latest non-merge subject is Chinese, write the new subject in Chinese; if it is English, write it in English. Preserve a recognizable local style when practical.
4. Calculate the tag before committing, selecting `release`, `beta`, or `alpha` from the user's command. Include the publishing remote when configured, typically `origin`, so local and published tag names are both considered without overwriting an existing local tag:

   ```powershell
   .\.venv\Scripts\python.exe .agent\skills\deploy\scripts\next_tag.py release --remote origin
   .\.venv\Scripts\python.exe .agent\skills\deploy\scripts\next_tag.py beta --remote origin
   .\.venv\Scripts\python.exe .agent\skills\deploy\scripts\next_tag.py alpha --remote origin
   ```

   If no repository `.venv` exists, run the script with available Python. If remote tag lookup fails, stop before creating a version tag rather than guessing from stale local tags.
5. Stage only the intended files, inspect `git diff --cached`, then create the commit:

   ```powershell
   git add -- <intended-files>
   git diff --cached --stat
   git diff --cached
   git commit -m "<message in matching language>"
   ```

   Do not create an empty commit unless the user explicitly requests it.
6. Create an annotated tag on the newly created commit, matching this repository's existing tags.
   In this repository, complete the CHANGELOG step below **before** running `git tag`:

   ```powershell
   git tag -a "<calculated-tag>" -m "<calculated-tag>"
   git show --no-patch --decorate HEAD
   ```

7. Push the commit and annotated tag to the publishing remote, typically `origin`, unless the user explicitly requested local-only operation:

   ```powershell
   git push origin HEAD "<calculated-tag>"
   ```

   Verify that the push succeeded before reporting the deployment complete.

8. Report the commit subject, tag, remote, and whether the push succeeded.

## 本仓库的额外顺序：打 tag 前先写 CHANGELOG

启动器的「关于 / 更新成功」弹窗只读**目标 tag 里**的 `CHANGELOG.md` / `CHANGELOG.zh-CN.md`。缺当前版本的 `## vX.Y.Z` 段落，就整页退回显示更新仓库的 `sync vX` 提交（v0.2.5 / v0.2.6 连续两次）。

所以用户说「发版 / 打 tag」时，**在步骤 6 创建 tag 之前**：

1. 用步骤 4 算出的版本号，给两个 CHANGELOG 各加一段 `## vX.Y.Z`，写用户可见的变化；不要写 sync / CI / 文档 / 测试这类内部提交。
2. 把两个文件 stage 进本次发布提交（可以和代码同一个提交，也可以单独提交），确认 `git show <tag>:CHANGELOG.zh-CN.md` 能看到这一段。
3. 再创建并推送 tag。

`build.yml` 的 `Check changelog for tag` 只是最后一道兜底；**不要等它报错**——那时 tag 已经推出去了。用户说 tag 的时候就把这两步做完再 tag。

## 本仓库的额外顺序：推 tag 后立刻同步更新仓库（GitHub / CNB）

`build.yml` 打包 China profile 时，要克隆更新仓库 `TieLaa/ok-hacking-update` 并检出**同名 tag**；该仓库只由它自己的 `sync.yml` 从源仓库拉 tag。同步慢了，构建就会失败在 `Setup failed: Tag '<tag>' not found`（v0.1.14 首次构建即如此）。

`sync.yml` 最后会把 main 和所有 tag 强推到 CNB 的 `TieLaa/ok-hacking-update`，所以这一步同时决定 GitHub 和 CNB 两个更新源有没有新 tag。源仓库推 tag **不会**自动触发它，必须手动跑。

推完 tag **立即**执行，不要等构建：

```powershell
gh workflow run sync.yml --repo TieLaa/ok-hacking-update -f tag=<tag>
```

然后确认两个更新源都有这个 tag：

```powershell
git ls-remote https://github.com/TieLaa/ok-hacking-update.git "refs/tags/<tag>"
git ls-remote https://cnb.cool/TieLaa/ok-hacking-update.git "refs/tags/<tag>"
```

顺序必须是「先推 tag，再 sync」：`sync.yml` 从 GitHub 读 tag，没推上去就看不到。sync 约 20 秒，而 `build.yml` 的 China 打包在约 12 分钟后才执行，所以来得及。构建已经失败的话，sync 完成后 `gh run rerun <run-id> --failed` 即可，不用重打 tag。构建成功后才能报「发布完成」。

## 构建后的 CNB 安装包

`export-to-cnb.yml` 在 Build 成功后自动把 China 安装包上传成 `TieLaa/ok-hacking-dist` 的 Release 附件（GitHub Release 的 `*-China-setup.exe` → CNB Release）。发布完成前确认它成功：

```powershell
gh run list --repo TieLaa/ok-hacking --workflow "Export release to cnb" --limit 3
```

本次 tag 的 run 失败或找不到时手动补跑：

```powershell
gh workflow run export-to-cnb.yml --repo TieLaa/ok-hacking -f tag=<tag>
```

直链（公开仓库匿名可下载）应为：

```text
https://cnb.cool/TieLaa/ok-hacking-dist/-/releases/download/<tag>/ok-hacking-win32-China-setup.exe
```

只有 Build 成功、CNB 更新仓库有 tag、CNB Release 附件存在，才能报「发布完成」。

## Guardrails

- Never rewrite, move, or delete an existing tag as part of deployment.
- Never include merge commits when choosing the commit-message language.
- Never infer a successful release from a local tag alone; report push success separately from CI publishing.
- Do not push when the user explicitly requests a local-only commit or tag.
- Keep stable, beta, and alpha numbering independent except that the latest stable release closes older or equal prerelease base versions.
