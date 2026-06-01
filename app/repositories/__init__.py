"""数据访问层。每个聚合一个 repo，实现 domain.ports 的对应接口。

约定：函数只接 ``conn``，不暴露 cursor、不管理事务；SQL 为模块常量；
行→对象映射用 psycopg.rows.class_row 或显式构造 domain 实体。
"""
