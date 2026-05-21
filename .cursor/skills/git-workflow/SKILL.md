---
name: git-workflow
description: >-
  Runs Git operations from natural language (status, diff, branch, commit, push,
  pull, merge, stash, log, remote, PR via gh). Use when the user mentions git,
  提交, 推送, 分支, 合并, 暂存, 回滚, commit, push, branch, merge, stash, rebase,
  pull request, or wants to inspect or change repository history.
---

# Git 工作流（自然语言 → 安全执行）

用户用中文或英文描述 Git 意图时，**先读本 Skill**，再按步骤用 **Shell** 在仓库根目录执行命令。不要猜测路径；先 `git status` / `git rev-parse --show-toplevel` 确认在正确仓库。

## 意图对照（常见说法）

| 用户可能说 | 动作 |
|------------|------|
| 看看改了什么 / 状态 | `git status`；需要细节时 `git diff` 与 `git diff --staged` |
| 暂存全部 / 只暂存某文件 | `git add`（按用户指定路径，勿盲目 `git add -A` 除非明确要求） |
| 提交 / commit | 见下方 **提交流程**（须用户明确要求才 commit） |
| 推送到远程 / push | `git push`（见 **推送安全**） |
| 拉取 / 更新代码 | `git pull`（优先说明是否 rebase） |
| 新建分支 / 切换分支 | `git switch -c` / `git switch` |
| 合并某分支 | `git merge`（不用 `-i` 交互命令） |
| 暂存工作区 / stash | `git stash push -m "..."` |
| 看历史 / 谁改的 | `git log --oneline -n 20` 等 |
| 撤销工作区修改 | `git restore <path>`（先确认，避免误删） |
| 创建 PR | 用 `gh`（见 **Pull Request**） |
| 和远程差多少 | `git fetch` 后 `git status` / `git log @{u}..HEAD` |

意图不清时：**只读命令**（status/diff/log）先跑，再向用户确认破坏性操作。

## 安全协议（必须遵守）

1. **禁止** `git config` 修改（全局或本地），除非用户明确要求且仅改明确项。
2. **禁止** `git push --force` / `-f` 到 `main`/`master`；其它分支 force push 须用户**明确书面同意**。
3. **禁止** `git reset --hard`、`git clean -fd` 除非用户明确要丢弃且已说明后果。
4. **禁止** `git commit` 除非用户**明确要求提交**（「帮我提交」「commit 一下」等）。仅「改了代码」≠ 要提交。
5. **禁止** `--no-verify` / `--no-gpg-sign`，除非用户明确要求跳过 hook。
6. **禁止** `git rebase -i` 及一切需交互输入的命令（`-i` flag）。
7. **amend**：仅当用户明确要求 amend，且 HEAD 为本会话创建、未 push，或用户接受 force push 时才 `git commit --amend`。
8. **hook 失败**：修复后**新 commit**，不要 amend 已失败的那次提交。
9. **秘密文件**：勿提交 `.env`、密钥、`credentials`；若 staged 含此类文件，警告并 `git restore --staged`。
10. 提交说明用 HEREDOC（PowerShell 可用 here-string 或 `git commit -F -` 从 stdin），保证多行消息格式正确。

## 提交流程（用户要求 commit 时）

并行执行（只读）：

```bash
git status
git diff
git diff --staged
git log -5 --oneline
```

1. 根据 diff **起草 1～2 句**提交说明（说 why，不说流水账）。
2. 仅 `git add` 与本次任务相关的文件；不要提交无关或 secret 文件。
3. 顺序执行：

```bash
git add <paths>
git commit -m "$(cat <<'EOF'
<message>

EOF
)"
git status
```

Windows PowerShell 若 HEREDOC 不可用：`git commit -m "单行摘要"` 或 `Set-Content -Path .git/COMMIT_EDITMSG -Value ...` 后 `git commit -F .git/COMMIT_EDITMSG`。

4. commit 失败（hook 拒绝）：修问题后**新建 commit**，不要 amend。

## 推送安全

- 先 `git status` 看是否 ahead/behind、是否跟踪远程分支。
- 无 upstream：`git push -u origin HEAD`（需网络权限）。
- 已 push 的 amend 需要 force push 时：**先警告**，未获明确同意不执行。

## Pull Request（GitHub）

用户要「开 PR / 提 PR」时：

1. 并行：`git status`、`git diff`、`git log main..HEAD`（或用户指定的 base 分支）、是否跟踪 remote。
2. 需要时 `git push -u origin HEAD`。
3. `gh pr create` 用 HEREDOC body；**不要**在未 push 时创建 PR。

```bash
gh pr create --title "..." --body "$(cat <<'EOF'
## Summary
- ...

## Test plan
- [ ] ...

EOF
)"
```

## 分支与合并

- 新建：`git switch -c <name>` 从当前 HEAD 或用户指定起点。
- 合并：`git merge <branch>`；有冲突时列出冲突文件，**不要**自动删用户代码；指导或按用户指示解决后继续。

## 输出给用户

- 用简短中文说明**做了什么、当前分支、是否已 push**。
- 附上关键命令输出中的结论（例如 latest commit hash、PR URL）。
-  destructive 操作前若未获确认，只给建议不执行。

## 更多细节

复杂场景（cherry-pick、恢复已删分支、子模块）见 [reference.md](reference.md)。
