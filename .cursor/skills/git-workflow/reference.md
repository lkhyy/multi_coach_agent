# Git 工作流 — 参考

## Cherry-pick

```bash
git fetch origin
git cherry-pick <commit>
# 冲突：解决后 git add . && git cherry-pick --continue
# 放弃：git cherry-pick --abort
```

## 查看某文件历史

```bash
git log --oneline -n 15 -- path/to/file
git blame path/to/file
```

## 比较分支

```bash
git fetch origin
git log --oneline origin/main..HEAD
git diff origin/main...HEAD
```

## 撤销最后一次 commit（保留改动）

仅用户明确要求且未 push 时：

```bash
git reset --soft HEAD~1
```

## 丢弃未暂存修改（危险）

仅用户明确确认：

```bash
git restore path/to/file
```

## 子模块

```bash
git submodule status
git submodule update --init --recursive
```

## Windows / PowerShell 提交多行消息

```powershell
$msg = @"
fix: short title

Longer body line.
"@
git commit -m $msg
```

## 与 multi_coach_agent 仓库相关

- 默认不提交：`backend/.env`、`backend/data/`、`backend/.env.example`（若已在 .gitignore）。
- 大模型权重、Chroma 数据目录勿 add。
