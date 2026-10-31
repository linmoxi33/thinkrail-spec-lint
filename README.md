# thinkrail-spec-lint

Offline spec-graph consistency linter for the JetBrains/thinkrail spec format — dangling links, duplicate ids, and parent cycles, with zero dependencies and no running IDE required.

**Why now:** thinkrail on today's GitHub Trending ships the *schema* and the *validation* (its `spec_validate` tool checks exactly these three things), but the validation only exists as an in-running agent tool inside the IDE. Nobody can run that same check offline — in CI, in a pre-commit hook, or on a clone that never opens the IDE.

Upstream: https://github.com/JetBrains/thinkrail

## 上游是什么、它缺什么

**ThinkRail** is a JetBrains in the trending editor built around the `pi` coding agent, managing git-worktree workspaces and a "spec-graph" — a set of markdown files whose YAML frontmatter (`id`, `type`, `title`, `parent`, `depends-on`, `references`, `implements`) forms a directed graph of the project's architecture.

It **already implements** a spec-graph consistency check — `validateGraph` in `packages/spec-graph/core/validate.ts` reports `danglingLinks` (links that point to a non-existent id), `duplicateIds` (an id declared in more than one file), and `parentCycles` (a parent chain that loops).

What it **lacks**: an offline entry point for that check. The validation is exposed **only** through the `spec_validate` tool (C-level evidence, `spec_validate` is registered via `pi.registerTool` in `packages/spec-graph/tools/validate.ts`) and the `spec-graph` package ships no `bin` in `packages/spec-graph/package.json`. There is no CLI, no pre-commit, no CI hook — the only path to the validation is starting the whole IDE and letting the agent run a tool. The single offline script in the repo, `check-spec-surface.ts`, validates a *different* thing (public-surface drift), not graph consistency.

证据（真实源码链接）：
- 校验实现 + 数据模型：`https://github.com/JetBrains/thinkrail/blob/main/packages/spec-graph/core/validate.ts`
- `spec_validate` 仅作为 agent 工具注册（无 CLI）：`https://github.com/JetBrains/thinkrail/blob/main/packages/spec-graph/tools/validate.ts`
- frontmatter schema 常量与解析：`https://github.com/JetBrains/thinkrail/blob/main/packages/spec-graph/core/parse.ts`
- `spec-graph` 包无 `bin`：`https://github.com/JetBrains/thinkrail/blob/main/packages/spec-graph/package.json`

## 这个工具做什么、不做什么

做：
- 遍历一个目录，找出所有「frontmatter 带 `id` + `type`」的 spec 文件（与上游 `FIELDS`/`REQUIRED_FIELDS` 规则同源，文件即被认定 spec）。
- 跳过 `.git` / `node_modules` / `dist` / `build` / `.claude` / `.superpowers` / `.gsd` / `.mockups` / `.playwright-mcp`（与上游派生索引的跳过清单一致）。
- 复刻 `validateGraph` 的三类校验：dangling links、duplicate ids、parent cycles。
- 输出人类可读报告和 `--json` 机器输出，退出码 `0/1/2`。

不做：
- 不启动 IDE、不跑 `pi` agent、不解析也可 **不联网**。
- 不做 spec 的创建/更新/删除（那是 `spec_create`/`spec_update` 的活）。
- 不做正文语法渲染，只读 frontmatter。
- 不是一个 schema 检查器，不校验 `type`/`status` 是否在枚举内（上游 `spec_validate` 也不查这个）。

## 安装 / 用法

零依赖（Python 3 标准库），直接把 `thinkrail_spec_lint.py`（或用 `bin/thinkrail-spec-lint`）拷进 PATH 即可。

```bash
# lint 一个目录（默认当前目录）
python3 thinkrail_spec_lint.py path/to/specs
# 机器可读输出
python3 thinkrail_spec_lint.py --json path/to/specs
# lint 单个文件
python3 thinkrail_spec_lint.py path/to/file.md
```

`--json` 输出示例（`examples/out/good.json` / `examples/out/bad.json`）：

```json
{
  "valid": false,
  "reports": [
    {
      "root": "examples/in/badproj",
      "specFiles": 3,
      "specCount": 2,
      "danglingLinks": [
        {"from": "stale", "fromPath": "examples/in/badproj/stale.md", "kind": "parent", "target": "nonexistent"}
      ],
      "duplicateIds": [
        {"id": "real", "paths": ["examples/in/badproj/dup.md", "examples/in/badproj/real.md"]}
      ],
      "parentCycles": []
    }
  ]
}
```

退出码：

| 码 | 含义 |
|---|---|
| `0` | 全部通过（无 dangling / duplicate / cycle） |
| `1` | 检出至少一个问题 |
| `2` | 用法或输入错误（路径不存在） |

## 局限

- 只做上游 `spec_validate` 已定义的三类校验；不查 frontmatter 字段枚举、`status` 合法值等（上游也没做）。
- 本工具用最小化 YAML frontmatter 解析（标量 + 内联列表），与上游 `yaml` 库对深层嵌套 frontmatter 的处理可能不一致——spec 格式按上游 skill 约定就是平铺标量/列表，够用。
- 逐文件读、整图构建；对超大型仓库（数十万 spec 文件）内存会线性增长，但 spec 集的典型规模远小于此。

## 本轮创新

**新在哪、和谁不一样、为什么是第一个填坑的：** 上游把 spec-graph 一致性校验 `validateGraph` 内嵌成仅供 IDE agent 调用的工具（`spec_validate`），整个仓库**没有任何离线/CLI 入口**（`spec-graph` 包无 `bin`，仓库内唯一离线脚本 `check-spec-surface.ts` 校验的是 API surface 漂移而非图一致性）。thinkrail-spec-lint 首次把这套图校验从 IDE 内部搬到离线 CLI + `--json`，让同一套规则能在 CI / pre-commit / 未经打开的 clone 上以 `0/1/2` 退出码阻断。已检索`gh search repos "<同类>"`：无成熟同类工具（详见站内 `阶段 D` 记录）。这是「把榜单项目自己都不敢测的那件事（离线一致性校验）放进 CI」的补位，非 README 复述、非换皮。