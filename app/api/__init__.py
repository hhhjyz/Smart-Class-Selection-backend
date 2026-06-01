"""接入层：FastAPI 依赖注入装配与 HTTP handlers。

handler 只做入参校验、RBAC 守卫、调 service、错误码映射，不含业务逻辑、
不直连 DB。依赖装配（把具体实现注入抽象 service）集中在 deps.py。
"""
