# Security Policy / 安全政策

## Supported versions / 支持的版本

| Version | Supported? / 是否支持 |
| --- | --- |
| Latest published release and `main` | Yes / 是 |
| Older releases | Best effort / 尽力支持 |

Self-hosted deployments should update to the latest published image or commit
before reporting an issue whenever practical.

自托管部署在条件允许时，请先升级到最新发布镜像或提交后再报告问题。

## Reporting a vulnerability / 报告安全漏洞

**Do not open a public issue, pull request, or discussion for a suspected
vulnerability.** Send a private report to
[sunxiaoyes@outlook.com](mailto:sunxiaoyes@outlook.com) with the subject
`[PanWatch Security]`. English and Simplified Chinese reports are both welcome.

**请勿通过公开 Issue、PR 或讨论披露疑似安全漏洞。** 请将报告私下发送至
[sunxiaoyes@outlook.com](mailto:sunxiaoyes@outlook.com)，邮件主题使用
`[PanWatch Security]`。欢迎使用英文或简体中文。

Please include, when available:

- a clear description of the issue and its potential impact;
- affected version, deployment method, and relevant configuration without
  secrets;
- reproducible steps or a minimal proof of concept;
- whether you have already disclosed the issue elsewhere; and
- a safe way to contact you for follow-up.

可提供时，请包含：问题与潜在影响的清晰描述、受影响版本与部署方式、去除敏感信息的
相关配置、复现步骤或最小 PoC、是否已在其他渠道披露，以及安全的回访方式。

Never include API keys, access tokens, cookies, personal access tokens,
portfolio data, customer data, or production credentials in a report.

报告中请勿包含 API Key、访问令牌、Cookie、个人访问令牌、持仓数据、用户数据或生产凭据。

## Response and disclosure / 响应与披露

We aim to acknowledge a complete report within five business days and will
coordinate a fix, verification, release notes, and disclosure timing with the
reporter. Please allow reasonable time for remediation before public disclosure.
PanWatch does not currently offer a bug-bounty program.

我们会尽力在五个工作日内确认信息完整的报告，并与报告者协调修复、验证、发布说明和
披露时间。请在公开披露前给予合理修复时间。PanWatch 当前不提供漏洞赏金计划。

## Scope / 范围

In scope are vulnerabilities in the PanWatch source code, official container
images, default configuration, and project-maintained workflows. Third-party AI
providers, market-data vendors, user-managed reverse proxies, and altered local
deployments may be outside direct remediation scope, but reports that explain
how PanWatch contributes to the impact are still useful.

范围包括 PanWatch 源码、官方容器镜像、默认配置和项目维护的工作流。第三方 AI 服务、
行情供应商、用户自行管理的反向代理或修改过的本地部署可能不在直接修复范围内；但只要
报告说明 PanWatch 如何导致或放大影响，我们仍欢迎提交。
