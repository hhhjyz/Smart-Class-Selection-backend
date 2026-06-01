"""APScheduler 后台任务：缓存刷新、Outbox 投递、对账。

仅在 worker 模式（MODE=worker）的进程内调度，api 进程不跑这些任务。
"""
