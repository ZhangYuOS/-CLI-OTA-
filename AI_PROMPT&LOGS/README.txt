txt文件的标题按顺序（first、second、...、sixth）是我每次给Claude Code的提示词

本次选用的模型是deepseek-v4-pro[1m]，其中的[1m]是上下文容量

AI_Prompt_Log.md文件记录了每次AI的修改记录

CLAUDE.md文件是给Claude Code的全局Prompt（Skills），防止项目跑偏、同时记录运行日志

我每次给Claude Code的命令是：@firstRequire Please write the code strictly in accordance with the requirements stated in this documents.
因为在Claude Code的命令行中写Prompt还是比较费劲的，不如直接写在文件中然后直接@